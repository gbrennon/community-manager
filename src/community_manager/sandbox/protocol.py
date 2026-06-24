"""Protocol for sandbox providers — Docker, Podman, QEMU."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SandboxConfig:
    """Shared configuration for any sandbox provider."""

    memory: str = "1g"
    cpus: int = 2
    network_enabled: bool = True    # start with network so npm install works
    workspace_dir: Path = field(default_factory=lambda: Path("/home/cline/project"))


@dataclass
class SandboxResult:
    """Result of running code inside a sandbox."""

    exit_code: int
    stdout: str
    stderr: str


class SandboxProvider(ABC):
    """Every provider (Docker, Podman, QEMU) must implement this."""

    config: SandboxConfig

    @abstractmethod
    async def launch(self) -> str:
        """Spin up a new sandbox. Returns a unique sandbox ID."""

    @abstractmethod
    async def copy_in(self, sandbox_id: str, host_path: Path, sandbox_path: Path) -> None:
        """Copy a file or directory from the host into the sandbox."""

    @abstractmethod
    async def exec(self, sandbox_id: str, command: list[str]) -> SandboxResult:
        """Run a command inside the sandbox."""

    @abstractmethod
    async def destroy(self, sandbox_id: str) -> None:
        """Stop and remove the sandbox."""

    @abstractmethod
    async def is_healthy(self, sandbox_id: str) -> bool:
        """Check whether the sandbox is still alive."""

    @abstractmethod
    async def disconnect_network(self, sandbox_id: str) -> None:
        """Sever all network access after install phase is complete."""
