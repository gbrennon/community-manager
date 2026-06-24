from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import shutil
import sys
from typing import Sequence

from community_manager.fetcher import GitHubIssueFetcher
from community_manager.sandbox.reviewer import IssueReviewer

_CONTAINER_RUNTIMES = ("podman", "docker")
_QEMU_BINARY = "qemu-system-x86_64"


def _first_available_container_runtime() -> str:
    for candidate in _CONTAINER_RUNTIMES:
        if shutil.which(candidate):
            return candidate
    return "docker"


def _qemu_is_installed() -> bool:
    return shutil.which(_QEMU_BINARY) is not None


def _any_container_runtime_installed() -> bool:
    return _first_available_container_runtime() != "docker" or shutil.which("docker")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="communiy-manager",
        description="Fetch and autonomously review Cline GitHub issues.",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    fetch_cmd = sub.add_parser("fetch", help="Fetch and display a GitHub issue")
    fetch_cmd.add_argument(
        "url", help="GitHub issue URL, e.g. https://github.com/cline/cline/issues/11761",
    )

    review_cmd = sub.add_parser(
        "review", help="Autonomously review a GitHub issue inside a sandbox",
    )
    review_cmd.add_argument("url", help="GitHub issue URL")
    review_cmd.add_argument(
        "--provider", choices=["container", "qemu", "auto"], default="auto",
        help="Sandbox backend: container (Docker/Podman), qemu (full VM), or auto (detect; default)",
    )
    review_cmd.add_argument(
        "--container-runtime", choices=["docker", "podman", "auto"], default="auto",
        help="Container runtime when --provider=container (default: auto-detect)",
    )
    review_cmd.add_argument(
        "--out", default="findings.md", help="Report output path (default: findings.md)",
    )

    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if argv and not argv[0].startswith("-") and argv[0] not in {"fetch", "review"}:
        argv = ["fetch"] + list(argv)

    args = parser.parse_args(argv)
    if args.command == "fetch" or (args.command is None and hasattr(args, "url")):
        _run_fetch(args.url, fetcher=fetcher)
    elif args.command == "review":
        _run_review(
            args.url, provider=args.provider,
            container_runtime=args.container_runtime,
            out=args.out, fetcher=fetcher,
        )
    else:
        parser.print_help()


def _run_fetch(url: str, *, fetcher: GitHubIssueFetcher | None = None) -> None:
    if fetcher is None:
        fetcher = GitHubIssueFetcher()
    try:
        issue = fetcher.fetch(url)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print("Title:", issue.title)
    print("State:", issue.state.value)
    print("---")
    print(issue.body or "(empty body)")


def _run_review(
    url: str,
    *,
    provider: str = "auto",
    container_runtime: str = "auto",
    out: str = "findings.md",
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
    from community_manager.sandbox.protocol import SandboxConfig
    from community_manager.sandbox.docker_provider import DockerProvider

    config = SandboxConfig()

    provider = _resolve_provider(provider)
    container_runtime = _resolve_container_runtime(container_runtime)

    if provider == "qemu":
        sandbox, backend_label = _build_qemu_provider(config)
    else:
        sandbox, backend_label = _build_container_provider(config, container_runtime)

    reviewer = IssueReviewer(
        provider=sandbox, fetcher=fetcher or GitHubIssueFetcher(),
    )

    async def review_and_print() -> None:
        print(f"Reviewing {url} ...")
        print(f"Sandbox: {backend_label}")
        verdict = await reviewer.review(url)
        _print_verdict(verdict)
        reviewer.write_report(verdict, Path(out))

    asyncio.run(review_and_print())


def _resolve_provider(raw: str) -> str:
    if raw != "auto":
        return raw
    if _any_container_runtime_installed():
        return "container"
    if _qemu_is_installed():
        return "qemu"
    print(
        "Error: no sandbox backend found. Install podman, docker,"
        " or qemu-system-x86_64.",
        file=sys.stderr,
    )
    sys.exit(1)


def _resolve_container_runtime(raw: str) -> str:
    return _first_available_container_runtime() if raw == "auto" else raw


def _build_qemu_provider(config: SandboxConfig) -> tuple[object, str]:
    if not _qemu_is_installed():
        print("Error: qemu-system-x86_64 not found on PATH", file=sys.stderr)
        sys.exit(1)
    from community_manager.sandbox.qemu_provider import QemuProvider

    qemu = QemuProvider(config=config)
    if not qemu.disk_image.exists():
        print(f"Error: QEMU disk image not found at {qemu.disk_image}", file=sys.stderr)
        sys.exit(1)
    return qemu, "qemu"


def _build_container_provider(config: SandboxConfig, runtime: str) -> tuple[object, str]:
    if not shutil.which(runtime):
        print(
            f"Error: {runtime} not found on PATH. Install podman or docker.",
            file=sys.stderr,
        )
        sys.exit(1)
    from community_manager.sandbox.docker_provider import DockerProvider

    return DockerProvider(config=config, binary=runtime), runtime


def _print_verdict(result: object) -> None:
    print()
    print("=" * 60)
    print(f"Title:       {result.issue_title}")
    print(f"Sandbox:     {result.sandbox_id}")
    print(f"Reproduced:  {result.reproduced}")
    print(f"Crash:       {result.crash_observed}")
    print()
    print("VERDICT:")
    print(result.verdict)
    print()
    if result.errors:
        print("Errors:")
        for error in result.errors:
            print(f"  - {error}")
