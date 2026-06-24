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
        "--debug", action="store_true",
        help="Print every action as it happens",
    )
    review_cmd.add_argument(
        "--provider", choices=["container", "qemu", "auto"], default="auto",
        help="Sandbox backend (default: auto-detect)",
    )
    review_cmd.add_argument(
        "--container-runtime", choices=["docker", "podman", "auto"], default="auto",
        help="Container runtime (default: auto-detect)",
    )
    review_cmd.add_argument(
        "--out", default="findings.md", help="Report output path",
    )

    batch_cmd = sub.add_parser(
        "batch", help="Review a range of issues from a GitHub repo",
    )
    batch_cmd.add_argument(
        "repo", help="GitHub owner/repo, e.g. cline/cline",
    )
    batch_cmd.add_argument(
        "start", type=int, help="First issue number",
    )
    batch_cmd.add_argument(
        "end", type=int, help="Last issue number (inclusive)",
    )
    batch_cmd.add_argument(
        "--debug", action="store_true",
        help="Print every action as it happens",
    )
    batch_cmd.add_argument(
        "--provider", choices=["container", "qemu", "auto"], default="auto",
        help="Sandbox backend (default: auto-detect)",
    )
    batch_cmd.add_argument(
        "--container-runtime", choices=["docker", "podman", "auto"], default="auto",
        help="Container runtime (default: auto-detect)",
    )
    batch_cmd.add_argument(
        "--concurrent", type=int, default=4,
        help="Max concurrent reviews (default: 4)",
    )
    batch_cmd.add_argument(
        "--out-dir", default="reports", help="Directory for per-issue reports",
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
    if argv and not argv[0].startswith("-") and argv[0] not in {"fetch", "review", "batch"}:
        argv = ["fetch"] + list(argv)

    args = parser.parse_args(argv)
    if args.command == "fetch" or (args.command is None and hasattr(args, "url")):
        _run_fetch(args.url, fetcher=fetcher)
    elif args.command == "review":
        _run_review(
            args.url,
            debug=getattr(args, "debug", False),
            provider=args.provider,
            container_runtime=args.container_runtime,
            out=args.out,
            fetcher=fetcher,
        )
    elif args.command == "batch":
        _run_batch(
            args.repo, args.start, args.end,
            debug=getattr(args, "debug", False),
            provider=args.provider,
            container_runtime=args.container_runtime,
            concurrent=args.concurrent,
            out_dir=args.out_dir,
            fetcher=fetcher,
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
    debug: bool = False,
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

    if debug:
        print(f"[debug] provider={provider} runtime={container_runtime}")

    reviewer = IssueReviewer(
        provider=sandbox, fetcher=fetcher or GitHubIssueFetcher(), debug=debug,
    )

    async def review_and_print() -> None:
        print(f"Reviewing {url} ...")
        print(f"Sandbox: {backend_label}")
        verdict = await reviewer.review(url)
        _print_verdict(verdict)
        reviewer.write_report(verdict, Path(out))

    asyncio.run(review_and_print())


def _run_batch(
    repo: str,
    start: int,
    end: int,
    *,
    debug: bool = False,
    provider: str = "auto",
    container_runtime: str = "auto",
    concurrent: int = 4,
    out_dir: str = "reports",
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
    from community_manager.sandbox.protocol import SandboxConfig
    from community_manager.sandbox.docker_provider import DockerProvider

    fetcher = fetcher or GitHubIssueFetcher()
    config = SandboxConfig()
    provider = _resolve_provider(provider)
    container_runtime = _resolve_container_runtime(container_runtime)
    reports_dir = Path(out_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    if provider == "qemu":
        sandbox, backend_label = _build_qemu_provider(config)
    else:
        sandbox, backend_label = _build_container_provider(config, container_runtime)

    urls = [f"https://github.com/{repo}/issues/{n}" for n in range(start, end + 1)]

    if debug:
        print(f"[debug] repo={repo} range={start}-{end} provider={provider} runtime={container_runtime} concurrent={concurrent}")

    reviewer = IssueReviewer(
        provider=sandbox, fetcher=fetcher, debug=debug,
    )

    async def run_batch() -> None:
        print(f"Batch reviewing {len(urls)} issues ({repo} #{start}–#{end}) ...")
        print(f"Sandbox: {backend_label} | concurrent: {concurrent}")
        results = await reviewer.review_many(urls, max_concurrent=concurrent)

        for r in results:
            issue_num = r.issue_url.rstrip("/").rsplit("/", 1)[1]
            report_path = reports_dir / f"{issue_num}.md"
            reviewer.write_report(r, report_path)
            status = "CONFIRMED" if r.crash_observed else ("ok" if r.reproduced else "FAIL")
            print(f"  #{issue_num}  {r.issue_title[:60]:60s}  {status}")

        crashes = sum(1 for r in results if r.crash_observed)
        print(f"\nDone. {len(results)} reviews, {crashes} crashes confirmed. Reports in {reports_dir}/")

    asyncio.run(run_batch())


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
