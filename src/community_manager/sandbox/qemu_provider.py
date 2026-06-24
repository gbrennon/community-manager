"""QEMU sandbox provider — full kernel-level isolation."""

from __future__ import annotations

import asyncio
import base64
import uuid
from pathlib import Path

from community_manager.sandbox.protocol import (
    SandboxConfig, SandboxProvider, SandboxResult,
)


class QemuProvider(SandboxProvider):
    """Launch QEMU VMs with -net none. Communicates via serial socket."""

    disk_image: Path = Path("cline-review.qcow2")
    serial_socket_dir: Path = Path("/tmp")

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()

    async def launch(self) -> str:
        sandbox_id = f"cline-issue-{uuid.uuid4().hex[:12]}"
        sock = self.serial_socket_dir / f"{sandbox_id}.sock"
        net = ["-net", "none"] if not self.config.network_enabled else []
        proc = await asyncio.create_subprocess_exec(
            "qemu-system-x86_64", "-enable-kvm",
            "-m", self.config.memory.removesuffix("g") + "G",
            "-smp", str(self.config.cpus),
            "-drive", f"file={self.disk_image},if=virtio",
            *net,
            "-serial", f"unix:{sock},server,nowait",
            "-nographic", "-daemonize",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"QEMU launch failed: {stderr.decode().strip()}")
        await self._wait_healthy(sandbox_id, sock)
        return sandbox_id

    async def copy_in(self, sid: str, host: Path, sandbox: Path) -> None:
        sock = self._socket_for(sid)
        proc = await asyncio.create_subprocess_exec(
            "tar", "-czf", "-", "-C", str(host.parent), host.name,
            stdout=asyncio.subprocess.PIPE,
        )
        data, _ = await proc.communicate()
        b64 = base64.b64encode(data).decode()
        await self._socket_cmd(sock, f"echo '{b64}' | base64 -d | tar -xzf - -C {sandbox.parent}")

    async def exec(self, sid: str, cmd: list[str]) -> SandboxResult:
        sock = self._socket_for(sid)
        escaped = " ".join(cmd)
        raw = await self._socket_cmd(
            sock, f"cd {self.config.workspace_dir} && {escaped}; echo EXIT:$?"
        )
        lines = raw.split("\n")
        exit_code = 0
        out_lines: list[str] = []
        for line in lines:
            if line.startswith("EXIT:"):
                exit_code = int(line.removeprefix("EXIT:"))
            else:
                out_lines.append(line)
        return SandboxResult(exit_code=exit_code, stdout="\n".join(out_lines), stderr="")

    async def destroy(self, sid: str) -> None:
        sock = self._socket_for(sid)
        try:
            await self._socket_cmd(sock, "sudo poweroff", timeout=5.0)
        except Exception:
            pass
        sock.unlink(missing_ok=True)

    async def is_healthy(self, sid: str) -> bool:
        sock = self._socket_for(sid)
        if not sock.exists():
            return False
        try:
            r = await self._socket_cmd(sock, "echo ok", timeout=2.0)
            return "ok" in r
        except Exception:
            return False

    def _socket_for(self, sid: str) -> Path:
        return self.serial_socket_dir / f"{sid}.sock"

    async def _socket_cmd(self, sock: Path, cmd: str, timeout: float = 30.0) -> str:
        proc = await asyncio.create_subprocess_exec(
            "socat", "-", f"UNIX-CONNECT:{sock}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=(cmd + "\n").encode()), timeout=timeout
            )
            return stdout.decode()
        except asyncio.TimeoutError:
            proc.kill(); await proc.wait()
            raise

    async def _wait_healthy(self, sid: str, sock: Path, timeout: float = 90.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if sock.exists():
                try:
                    if await self.is_healthy(sid):
                        return
                except Exception:
                    pass
            await asyncio.sleep(1.0)
        raise TimeoutError(f"QEMU {sid} not responding within {timeout}s")
