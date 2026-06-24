"""Sandbox provider backed by Docker or Podman."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from community_manager.sandbox.protocol import (
    SandboxConfig,
    SandboxProvider,
    SandboxResult,
)

_HEALTH_CHECK_POLL_SECONDS = 0.3
_DEFAULT_HEALTH_TIMEOUT_SECONDS = 30.0
_STATE_RUNNING_MARKERS = frozenset({"true", "running"})


class DockerProvider(SandboxProvider):
    """Network-isolated container sandbox — works with Docker and Podman.

    Set ``binary`` to ``"podman"`` for rootless Podman support.
    """

    image: str = "cline-review-sandbox"
    binary: str = "docker"

    def __init__(
        self, config: SandboxConfig | None = None, *, binary: str = "docker",
    ) -> None:
        self.config = config or SandboxConfig()
        self.binary = binary

    async def launch(self) -> str:
        sandbox_id = f"cline-issue-{uuid.uuid4().hex[:12]}"
        await self._start_container(sandbox_id)
        await self._ensure_healthy(sandbox_id, _DEFAULT_HEALTH_TIMEOUT_SECONDS)
        return sandbox_id

    async def copy_in(self, sandbox_id: str, host_path: Path, sandbox_path: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "cp", str(host_path), f"{sandbox_id}:{sandbox_path}",
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"{self.binary} cp failed for {sandbox_id}")

    async def exec(self, sandbox_id: str, command: list[str]) -> SandboxResult:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "exec", "--workdir", str(self.config.workspace_dir),
            sandbox_id, *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return SandboxResult(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode(),
            stderr=stderr.decode(),
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

    async def _start_container(self, sandbox_id: str) -> None:
        network_flag = [] if self.config.network_enabled else ["--network", "none"]
        proc = await asyncio.create_subprocess_exec(
            self.binary, "run", "--detach", "--rm",
            *network_flag,
            "--memory", self.config.memory,
            "--cpus", str(self.config.cpus),
            "--name", sandbox_id,
            self.image,
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
