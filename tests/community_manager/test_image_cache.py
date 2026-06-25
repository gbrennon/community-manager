"""Tests for the sandbox image cache."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from community_manager.sandbox.image_cache import IMAGE_PREFIX, ImageCache


class TestImageName:
    def test_versioned(self) -> None:
        assert ImageCache().image_name("3.0.29") == f"{IMAGE_PREFIX}:3.0.29"

    def test_empty_version_falls_back_to_latest(self) -> None:
        assert ImageCache().image_name("") == f"{IMAGE_PREFIX}:latest"

    def test_whitespace_falls_back_to_latest(self) -> None:
        assert ImageCache().image_name("  ") == f"{IMAGE_PREFIX}:latest"


class TestExists:
    @pytest.mark.asyncio
    async def test_returns_true_when_found(self) -> None:
        cache = ImageCache(binary="docker")
        proc = MagicMock(returncode=0)
        proc.wait = AsyncMock(return_value=None)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            assert await cache.exists("3.0.29") is True

    @pytest.mark.asyncio
    async def test_returns_false_when_missing(self) -> None:
        cache = ImageCache(binary="docker")
        proc = MagicMock(returncode=1)
        proc.wait = AsyncMock(return_value=None)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            assert await cache.exists("3.0.29") is False

    @pytest.mark.asyncio
    async def test_returns_false_when_disabled(self) -> None:
        cache = ImageCache(enabled=False)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            result = await cache.exists("3.0.29")
        mock_exec.assert_not_called()
        assert result is False


class TestCommit:
    @pytest.mark.asyncio
    async def test_commits_when_image_missing(self) -> None:
        cache = ImageCache(binary="docker")
        call_count = 0

        miss = MagicMock(returncode=1)
        miss.wait = AsyncMock(return_value=None)
        ok = MagicMock(returncode=0)
        ok.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return miss if "inspect" in args else ok

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await cache.commit("sandbox-abc", "3.0.29")

        assert call_count == 2  # inspect + commit

    @pytest.mark.asyncio
    async def test_skips_commit_when_already_exists(self) -> None:
        cache = ImageCache(binary="docker")
        hit = MagicMock(returncode=0)
        hit.wait = AsyncMock(return_value=None)

        with patch("asyncio.create_subprocess_exec", return_value=hit) as mock_exec:
            await cache.commit("sandbox-abc", "3.0.29")

        assert mock_exec.call_count == 1
        assert "inspect" in mock_exec.call_args[0]

    @pytest.mark.asyncio
    async def test_does_nothing_when_disabled(self) -> None:
        cache = ImageCache(enabled=False)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            await cache.commit("sandbox-abc", "3.0.29")
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_fatal_on_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        cache = ImageCache(binary="docker")
        miss = MagicMock(returncode=1)
        miss.wait = AsyncMock(return_value=None)
        fail = MagicMock(returncode=1)
        fail.communicate = AsyncMock(return_value=(b"", b"something went wrong"))

        async def fake_exec(*args, **kwargs):
            return miss if "inspect" in args else fail

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await cache.commit("sandbox-abc", "3.0.29")  # must not raise

        assert "WARNING" in capsys.readouterr().err


class TestConcurrentCommit:
    @pytest.mark.asyncio
    async def test_lock_prevents_double_commit(self) -> None:
        cache = ImageCache(binary="docker")
        commit_calls = 0

        miss = MagicMock(returncode=1)
        miss.wait = AsyncMock(return_value=None)
        hit = MagicMock(returncode=0)
        hit.wait = AsyncMock(return_value=None)

        async def fake_exec(*args, **kwargs):
            nonlocal commit_calls
            if "inspect" in args:
                return miss if commit_calls == 0 else hit
            commit_calls += 1
            proc = MagicMock(returncode=0)
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await asyncio.gather(
                cache.commit("sandbox-1", "3.0.29"),
                cache.commit("sandbox-2", "3.0.29"),
            )

        assert commit_calls == 1
