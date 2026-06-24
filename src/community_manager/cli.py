from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from typing import Sequence

from community_manager.fetcher import GitHubIssueFetcher
from community_manager.sandbox.reviewer import IssueReviewer

_CONTAINER_CANDIDATES = ("podman", "docker")


def _detect_container_runtime() -> str:
    """Return the best available container runtime (podman > docker)."""
    for c in _CONTAINER_CANDIDATES:
        if shutil.which(c):
            return c
    return "docker"


def _detect_qemu() -> bool:
    """Check whether qemu-system-x86_64 is on PATH."""
    return shutil.which("qemu-system-x86_64") is not None


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

    # Resolve provider: prefer containers (lighter, no disk image needed).
    # Fall back to QEMU only when no container runtime is available.
    has_container = _detect_container_runtime() != "docker" or shutil.which("docker")
    has_qemu = _detect_qemu()

    if provider == "auto":
        if has_container:
            provider = "container"
        elif has_qemu:
            provider = "qemu"
        else:
            print(
                "Error: no sandbox backend found. Install podman, docker,"
                " or qemu-system-x86_64.",
                file=sys.stderr,
            )
            sys.exit(1)

    if container_runtime == "auto":
        container_runtime = _detect_container_runtime()

    if provider == "qemu":
        if not has_qemu:
            print("Error: qemu-system-x86_64 not found on PATH", file=sys.stderr)
            sys.exit(1)
        from community_manager.sandbox.qemu_provider import QemuProvider

        qemu_prov = QemuProvider(config=config)
        if not qemu_prov.disk_image.exists():
            print(
                f"Error: QEMU disk image not found at {qemu_prov.disk_image}",
                file=sys.stderr,
            )
            sys.exit(1)
        backend_label = "qemu"
        prov: object = qemu_prov
    else:
        if not shutil.which(container_runtime):
            print(
                f"Error: {container_runtime} not found on PATH."
                " Install podman or docker.",
                file=sys.stderr,
            )
            sys.exit(1)
        prov = DockerProvider(config=config, binary=container_runtime)
        backend_label = container_runtime

    reviewer = IssueReviewer(
        provider=prov,  # type: ignore[arg-type]
        fetcher=fetcher or GitHubIssueFetcher(),
    )

    async def _do() -> None:
        print(f"Reviewing {url} ...")
        print(f"Sandbox: {backend_label}")
        result = await reviewer.review(url)
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
            for e in result.errors:
                print(f"  - {e}")
        reviewer.write_report(result)
        print(f"\nReport written to {out}")

    asyncio.run(_do())
