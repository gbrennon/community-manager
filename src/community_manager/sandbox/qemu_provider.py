"""Sandbox provider backed by QEMU — full kernel-level isolation."""

from __future__ import annotations

import asyncio
import base64
import uuid
from pathlib import Path

from community_manager.sandbox.protocol import (
    SandboxConfig, SandboxProvider, SandboxResult,
)

_QEMU_BINARY = "qemu-system-x86_64"
_SOCAT_BINARY = "socat"
_BOOT_TIMEOUT_SECONDS = 90.0
_BOOT_POLL_SECONDS = 1.0
_COMMAND_TIMEOUT_SECONDS = 30.0
_EXIT_MARKER = "EXIT:"


class QemuProvider(SandboxProvider):
    """Launch QEMU VMs with -net none. Communicates via Unix serial socket."""

    disk_image: Path = Path("cline-review.qcow2")
    serial_socket_dir: Path = Path("/tmp")

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()

    async def launch(self) -> str:
        sandbox_id = f"cline-issue-{uuid.uuid4().hex[:12]}"
        socket = self._socket_for(sandbox_id)

        if not self.disk_image.exists():
            raise RuntimeError(f"QEMU disk image not found: {self.disk_image}")

        proc = await asyncio.create_subprocess_exec(
            _QEMU_BINARY, "-enable-kvm",
            "-m", self.config.memory.removesuffix("g") + "G",
            "-smp", str(self.config.cpus),
            "-drive", f"file={self.disk_image},if=virtio",
            *self._network_flags(),
            "-serial", f"unix:{socket},server,nowait",
            "-display", "none",
            "-daemonize",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"QEMU launch failed: {stderr.decode().strip()}")
        await self._wait_for_boot(sandbox_id, socket)
        return sandbox_id

    async def copy_in(self, sid: str, host: Path, sandbox: Path) -> None:
        socket = self._socket_for(sid)
        data = await self._tar_directory(host)
        await self._send_via_socket(
            socket,
            f"echo '{base64.b64encode(data).decode()}' | base64 -d | tar -xzf - -C {sandbox.parent}",
        )

    async def exec(self, sid: str, command: list[str]) -> SandboxResult:
        socket = self._socket_for(sid)
        raw = await self._send_via_socket(
            socket,
            f"cd {self.config.workspace_dir} && {' '.join(command)}; echo {_EXIT_MARKER}$?",
        )
        exit_code = 0
        output_lines: list[str] = []
        for line in raw.split("\n"):
            if line.startswith(_EXIT_MARKER):
                exit_code = int(line.removeprefix(_EXIT_MARKER))
            else:
                output_lines.append(line)
        return SandboxResult(exit_code=exit_code, stdout="\n".join(output_lines), stderr="")

    async def destroy(self, sid: str) -> None:
        socket = self._socket_for(sid)
        try:
            await self._send_via_socket(socket, "sudo poweroff", timeout=5.0)
        except Exception:
            pass
        socket.unlink(missing_ok=True)

    async def is_healthy(self, sid: str) -> bool:
        socket = self._socket_for(sid)
        if not socket.exists():
            return False
        try:
            response = await self._send_via_socket(socket, "echo ok", timeout=2.0)
            return "ok" in response
        except Exception:
            return False

    def _socket_for(self, sid: str) -> Path:
        return self.serial_socket_dir / f"{sid}.sock"

    def _network_flags(self) -> list[str]:
        return ["-net", "none"] if not self.config.network_enabled else []

    async def _tar_directory(self, path: Path) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            "tar", "-czf", "-", "-C", str(path.parent), path.name,
            stdout=asyncio.subprocess.PIPE,
        )
        data, _ = await proc.communicate()
        return data

    async def _send_via_socket(
        self, socket: Path, command: str, timeout: float = _COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        proc = await asyncio.create_subprocess_exec(
            _SOCAT_BINARY, "-", f"UNIX-CONNECT:{socket}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=(command + "\n").encode()), timeout=timeout,
            )
            return stdout.decode()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

    async def _wait_for_boot(self, sid: str, socket: Path) -> None:
        deadline = asyncio.get_running_loop().time() + _BOOT_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            if socket.exists():
                try:
                    if await self.is_healthy(sid):
                        return
                except Exception:
                    pass
            await asyncio.sleep(_BOOT_POLL_SECONDS)
        raise TimeoutError(f"QEMU {sid} not responding within {_BOOT_TIMEOUT_SECONDS}s")
