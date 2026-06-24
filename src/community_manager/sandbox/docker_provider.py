"""Docker sandbox provider — network-isolated TypeScript containers."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from community_manager.sandbox.protocol import (
    SandboxConfig,
    SandboxProvider,
    SandboxResult,
)


class DockerProvider(SandboxProvider):
    """Launch and manage network‑isolated Docker / Podman containers.

    Uses the ``node:22-slim`` image with TypeScript tooling pre‑installed
    so that ``cline`` can run inside.

    Set ``binary`` to ``"podman"`` for rootless Podman support.
    """

    docker_image: str = "cline-review-sandbox"
    binary: str = "docker"

    def __init__(
        self, config: SandboxConfig | None = None, *, binary: str = "docker",
    ) -> None:
        self.config = config or SandboxConfig()
        self.binary = binary

    async def launch(self) -> str:
        sandbox_id = f"cline-issue-{uuid.uuid4().hex[:12]}"
        network = [] if self.config.network_enabled else ["--network", "none"]

        proc = await asyncio.create_subprocess_exec(
            self.binary, "run", "--detach", "--rm",
            *network,
            "--memory", self.config.memory,
            "--cpus", str(self.config.cpus),
            "--name", sandbox_id,
            self.docker_image,
            "sleep", "infinity",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"{self.binary} launch failed: {stderr.decode().strip()}")

        await self._wait_healthy(sandbox_id)
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
        # Podman: --format uses .State.Status (not .State.Running alias)
        fmt = "{{.State.Status}}" if self.binary == "podman" else "{{.State.Running}}"
        proc = await asyncio.create_subprocess_exec(
            self.binary, "inspect", "--format", fmt, sandbox_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        val = stdout.decode().strip()
        return val in ("true", "running")

    async def _wait_healthy(self, sandbox_id: str, timeout: float = 30.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self.is_healthy(sandbox_id):
                return
            await asyncio.sleep(0.3)
        raise TimeoutError(f"Sandbox {sandbox_id} not healthy within {timeout}s")
