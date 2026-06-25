"""Sandbox provider backed by Docker or Podman."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path

from community_manager.sandbox.protocol import (
    SandboxConfig,
    SandboxProvider,
    SandboxResult,
)

_HEALTH_CHECK_POLL_SECONDS = 0.3
_DEFAULT_HEALTH_TIMEOUT_SECONDS = 30.0
_STATE_RUNNING_MARKERS = frozenset({"true", "running"})
_BASE_IMAGE = "node:22-slim"


class DockerProvider(SandboxProvider):
    """Ephemeral container sandbox — Docker or Podman."""

    binary: str = "docker"

    def __init__(
        self,
        config: SandboxConfig | None = None,
        *,
        binary: str = "docker",
        image_cache: object | None = None,
    ) -> None:
        self.config = config or SandboxConfig()
        self.binary = binary
        self.image_cache = image_cache  # ImageCache | None
        # Active image — overridden per-launch when a cached image is found.
        self._active_image: str = _BASE_IMAGE

    async def launch(self, *, cline_version: str = "") -> str:
        """Spin up a new sandbox container.

        If *cline_version* is provided and a cached image exists for it,
        we launch from that image so cline is already installed.
        """
        self._active_image = await self._resolve_image(cline_version)
        sandbox_id = f"cline-issue-{uuid.uuid4().hex[:12]}"
        await self._start_container(sandbox_id)
        await self._ensure_healthy(sandbox_id, _DEFAULT_HEALTH_TIMEOUT_SECONDS)
        return sandbox_id

    async def _resolve_image(self, cline_version: str) -> str:
        """Return the best available image for *cline_version*."""
        if self.image_cache and cline_version:
            if await self.image_cache.exists(cline_version):
                name = self.image_cache.image_name(cline_version)
                return name
        return _BASE_IMAGE

    @property
    def launched_from_cache(self) -> bool:
        """True if the last launch() used a pre-built cached image."""
        return self._active_image != _BASE_IMAGE

    async def copy_in(self, sandbox_id: str, host_path: Path, sandbox_path: Path) -> None:
        pass

    async def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        tty: bool = False,
        timeout: float = 120.0,
    ) -> SandboxResult:
        """Run *command* inside the sandbox and return the result.

        When *tty=True*, the command is wrapped with ``script -q -c '...'
        /dev/null`` inside the container.  This fakes a PTY from within so
        programs that check ``isatty()`` (like cline) see a real terminal,
        while the outer ``docker exec`` stays non-interactive and fully
        controllable.  Using ``docker exec -t`` is intentionally avoided
        because it leaks the host PTY into the pipe, producing CRLF output
        and causing ``communicate()`` to block after the process exits (the
        PTY master keeps the fd open).

        *timeout* is a hard wall-clock limit in seconds — the subprocess is
        killed and a TimeoutError propagated if it is exceeded.
        """
        if tty:
            # Build the inner shell invocation and escape single-quotes for
            # the outer sh -c '...' wrapper that script requires.
            inner = " ".join(command)
            inner_escaped = inner.replace("'", "'\\''")
            final_command = ["script", "-q", "-c", f"sh -c '{inner_escaped}'", "/dev/null"]
        else:
            final_command = command

        proc = await asyncio.create_subprocess_exec(
            self.binary, "exec", sandbox_id, *final_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(
                f"sandbox exec timed out after {timeout}s: {' '.join(command)}"
            )
        return SandboxResult(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n"),
            stderr=stderr.decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n"),
        )

    async def exec_streaming(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        on_line: Callable[[str], None],
        tick_interval: float = 15.0,
        on_tick: Callable[[float], None] | None = None,
    ) -> SandboxResult:
        """Run a command, calling on_line() for every output line in real time.

        on_tick is called every tick_interval seconds of silence so callers can
        log that the process is still alive (elapsed seconds passed as argument).
        """
        proc = await asyncio.create_subprocess_exec(
            self.binary, "exec", sandbox_id, *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge so order is preserved
        )
        assert proc.stdout is not None

        stdout_lines: list[str] = []
        elapsed = 0.0

        async def _drain() -> None:
            nonlocal elapsed
            last_tick = asyncio.get_running_loop().time()
            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=tick_interval,
                    )
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_running_loop().time() - last_tick + elapsed
                    if on_tick:
                        on_tick(elapsed)
                    continue
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").rstrip("\r\n")
                stdout_lines.append(line)
                on_line(line)

        await _drain()
        await proc.wait()
        return SandboxResult(
            exit_code=proc.returncode or 0,
            stdout="\n".join(stdout_lines),
            stderr="",
        )

    async def destroy(self, sandbox_id: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "stop", sandbox_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def is_healthy(self, sandbox_id: str) -> bool:
        state_format = "{{.State.Status}}" if self.binary == "podman" else "{{.State.Running}}"
        proc = await asyncio.create_subprocess_exec(
            self.binary, "inspect", "--format", state_format, sandbox_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() in _STATE_RUNNING_MARKERS

    async def disconnect_network(self, sandbox_id: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "network", "disconnect", "bridge", sandbox_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def _start_container(self, sandbox_id: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "run", "--detach", "--rm",
            "--memory", self.config.memory,
            "--cpus", str(self.config.cpus),
            "--name", sandbox_id,
            self._active_image,
            "sleep", "infinity",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"{self.binary} launch failed: {stderr.decode().strip()}")

    async def _ensure_healthy(self, sandbox_id: str, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self.is_healthy(sandbox_id):
                return
            await asyncio.sleep(_HEALTH_CHECK_POLL_SECONDS)
        raise TimeoutError(f"Sandbox {sandbox_id} not healthy within {timeout}s")
