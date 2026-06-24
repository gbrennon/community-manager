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
    """Launch and manage network‑isolated Docker containers.

    Uses the ``node:22-slim`` image with TypeScript tooling pre‑installed
    so that ``cline`` can run inside.
    """

    docker_image: str = "cline-review-sandbox"

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()

    async def launch(self) -> str:
        sandbox_id = f"cline-issue-{uuid.uuid4().hex[:12]}"
        network = [] if self.config.network_enabled else ["--network", "none"]

        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--detach", "--rm",
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
            raise RuntimeError(f"Docker launch failed: {stderr.decode().strip()}")

        await self._wait_healthy(sandbox_id)
        return sandbox_id

    async def copy_in(self, sandbox_id: str, host_path: Path, sandbox_path: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "cp", str(host_path), f"{sandbox_id}:{sandbox_path}",
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"docker cp failed for {sandbox_id}")

    async def exec(self, sandbox_id: str, command: list[str]) -> SandboxResult:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "--workdir", str(self.config.workspace_dir),
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
            "docker", "stop", sandbox_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def is_healthy(self, sandbox_id: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format", "{{.State.Running}}", sandbox_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() == "true"

    async def _wait_healthy(self, sandbox_id: str, timeout: float = 30.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self.is_healthy(sandbox_id):
                return
            await asyncio.sleep(0.3)
        raise TimeoutError(f"Sandbox {sandbox_id} not healthy within {timeout}s")
