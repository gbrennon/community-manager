"""Image cache for pre-built cline sandbox images.

Strategy
--------
After a fresh ``npm install -g cline@X.Y.Z`` inside a container succeeds,
we snapshot that container into a local image named
``cline-review-sandbox:X.Y.Z`` via ``docker commit``.

The next time any review needs the same version we detect the image with
``docker image inspect`` and launch directly from it — skipping the
2–5 min npm download entirely.

A per-version ``asyncio.Lock`` ensures that concurrent reviews of the same
version never race to commit the same image twice.
"""

from __future__ import annotations

import asyncio
import sys
from typing import ClassVar


IMAGE_PREFIX = "cline-review-sandbox"
_FALLBACK_VERSION_TAG = "latest"


class ImageCache:
    """Manages pre-built Docker / Podman images with cline pre-installed."""

    # Shared lock registry — one Lock per (binary, version) pair so concurrent
    # reviews of the same version don't race each other.
    _locks: ClassVar[dict[tuple[str, str], asyncio.Lock]] = {}

    def __init__(self, binary: str = "docker", *, enabled: bool = True) -> None:
        self.binary = binary
        self.enabled = enabled

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def image_name(self, cline_version: str) -> str:
        """Return the local image tag for a given cline version."""
        tag = cline_version.strip() if cline_version.strip() else _FALLBACK_VERSION_TAG
        return f"{IMAGE_PREFIX}:{tag}"

    async def exists(self, cline_version: str) -> bool:
        """Return True if a cached image for *cline_version* exists locally."""
        if not self.enabled:
            return False
        proc = await asyncio.create_subprocess_exec(
            self.binary, "image", "inspect", self.image_name(cline_version),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0

    async def commit(self, container_id: str, cline_version: str) -> None:
        """Snapshot *container_id* into a reusable local image.

        Uses a per-version lock so that concurrent reviews never issue
        two commits for the same version simultaneously.
        """
        if not self.enabled:
            return

        lock = self._get_lock(cline_version)
        async with lock:
            # Re-check inside the lock — another coroutine may have already
            # committed while we were waiting.
            if await self.exists(cline_version):
                return

            image = self.image_name(cline_version)
            proc = await asyncio.create_subprocess_exec(
                self.binary, "commit",
                "--message", f"cline {cline_version} pre-installed via npm",
                container_id, image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                msg = stderr.decode().strip()
                print(f"[cache] WARNING: docker commit failed: {msg}", file=sys.stderr)
                return  # non-fatal — next run will just re-install

            print(f"[cache] Image saved: {image}", flush=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_lock(self, cline_version: str) -> asyncio.Lock:
        key = (self.binary, cline_version)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]
