from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import shutil
import sys
from typing import Sequence

from community_manager.fetcher import GitHubIssueFetcher
from community_manager.notifications import build_notifier
from community_manager.sandbox.reviewer import IssueReviewer
from community_manager.sandbox.reviewer import ReviewResult

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
        "review", help="Review one or more issues by URL or ID",
    )
    review_cmd.add_argument(
        "targets", nargs="+",
        help="Full URLs like https://github.com/user/repo/issues/1 or bare numbers 1 2 3",
    )
    review_cmd.add_argument(
        "--repo", default=None,
        help="GitHub owner/repo when using bare issue numbers, e.g. cline/cline",
    )
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
        "--out", default="verdicts.md", help="Single report or summary file for multiple targets",
    )
    review_cmd.add_argument(
        "--concurrent", type=int, default=4,
        help="Max parallel reviews (default: 4)",
    )
    review_cmd.add_argument(
        "--no-cache", action="store_true",
        help="Disable the image cache — always do a fresh npm install",
    )

    batch_cmd = sub.add_parser(
        "batch", help="Review a consecutive range of issues",
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
        "--out", default="verdicts.md", help="Summary report file",
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
    if args.command == "fetch" or (args.command is None and hasattr(args, "targets")):
        _run_fetch(args.url, fetcher=fetcher)
    elif args.command == "review":
        _run_review(
            args.targets,
            repo=args.repo,
            debug=getattr(args, "debug", False),
            provider=args.provider,
            container_runtime=args.container_runtime,
            out=args.out,
            concurrent=args.concurrent,
            no_cache=getattr(args, "no_cache", False),
            fetcher=fetcher,
        )
    elif args.command == "batch":
        _run_batch_range(
            args.repo, args.start, args.end,
            debug=getattr(args, "debug", False),
            provider=args.provider,
            container_runtime=args.container_runtime,
            concurrent=args.concurrent,
            out=args.out,
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


def _normalize_repo(repo: str) -> str:
    """Normalize --repo to 'owner/repo' form, accepting full GitHub URLs too."""
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if repo.startswith(prefix):
            return repo[len(prefix):].rstrip("/")
    return repo.rstrip("/")


def _resolve_targets_to_urls(targets: list[str], repo: str | None) -> list[str]:
    """Convert target list (URLs or bare IDs) into full GitHub URLs."""
    normalized_repo = _normalize_repo(repo) if repo else None
    result: list[str] = []
    for t in targets:
        if t.startswith("https://"):
            result.append(t)
        elif normalized_repo:
            result.append(f"https://github.com/{normalized_repo}/issues/{t}")
        else:
            print(
                f"Error: '{t}' is not a full URL and --repo was not set.",
                file=sys.stderr,
            )
            sys.exit(1)
    return result


def _run_review(
    targets: list[str],
    *,
    repo: str | None = None,
    debug: bool = False,
    provider: str = "auto",
    container_runtime: str = "auto",
    out: str = "verdicts.md",
    concurrent: int = 4,
    no_cache: bool = False,
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
    urls = _resolve_targets_to_urls(targets, repo)
    fetcher = fetcher or GitHubIssueFetcher()

    provider = _resolve_provider(provider)
    container_runtime = _resolve_container_runtime(container_runtime)
    sandbox, backend_label = _build_sandbox(provider, container_runtime, no_cache=no_cache)

    reviewer = IssueReviewer(
        provider=sandbox, fetcher=fetcher, debug=debug,
    )

    async def run_all() -> None:
        print(f"Reviewing {len(urls)} issue(s) ...")
        print(f"Sandbox: {backend_label} | concurrent: {concurrent}")
        results = await reviewer.review_many(urls, max_concurrent=concurrent)
        report_path = Path(out)
        _write_verdicts_file(results, report_path)
        _print_summary(results)
        build_notifier().notify(results, report_path)

    asyncio.run(run_all())


def _run_batch_range(
    repo: str,
    start: int,
    end: int,
    *,
    debug: bool = False,
    provider: str = "auto",
    container_runtime: str = "auto",
    concurrent: int = 4,
    out: str = "verdicts.md",
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
    urls = [f"https://github.com/{repo}/issues/{n}" for n in range(start, end + 1)]
    fetcher = fetcher or GitHubIssueFetcher()

    provider = _resolve_provider(provider)
    container_runtime = _resolve_container_runtime(container_runtime)
    sandbox, backend_label = _build_sandbox(provider, container_runtime)

    reviewer = IssueReviewer(
        provider=sandbox, fetcher=fetcher, debug=debug,
    )

    async def run_all() -> None:
        print(f"Batch reviewing {len(urls)} issues ({repo} #{start}–#{end}) ...")
        print(f"Sandbox: {backend_label} | concurrent: {concurrent}")
        results = await reviewer.review_many(urls, max_concurrent=concurrent)
        report_path = Path(out)
        _write_verdicts_file(results, report_path)
        _print_summary(results)
        build_notifier().notify(results, report_path)

    asyncio.run(run_all())


def _build_sandbox(
    provider: str, container_runtime: str, *, no_cache: bool = False,
) -> tuple[object, str]:
    from community_manager.sandbox.protocol import SandboxConfig

    config = SandboxConfig()
    if provider == "qemu":
        return _build_qemu_provider(config)
    return _build_container_provider(config, container_runtime, no_cache=no_cache)


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


def _build_qemu_provider(config: object) -> tuple[object, str]:
    if not _qemu_is_installed():
        print("Error: qemu-system-x86_64 not found on PATH", file=sys.stderr)
        sys.exit(1)
    from community_manager.sandbox.qemu_provider import QemuProvider

    qemu = QemuProvider(config=config)
    if not qemu.disk_image.exists():
        print(f"Error: QEMU disk image not found at {qemu.disk_image}", file=sys.stderr)
        sys.exit(1)
    return qemu, "qemu"


def _build_container_provider(
    config: object, runtime: str, *, no_cache: bool = False,
) -> tuple[object, str]:
    if not shutil.which(runtime):
        print(
            f"Error: {runtime} not found on PATH. Install podman or docker.",
            file=sys.stderr,
        )
        sys.exit(1)
    from community_manager.sandbox.docker_provider import DockerProvider
    from community_manager.sandbox.image_cache import ImageCache

    cache = ImageCache(binary=runtime, enabled=not no_cache)
    return DockerProvider(config=config, binary=runtime, image_cache=cache), runtime


def _write_verdicts_file(results: list[ReviewResult], path: Path) -> None:
    lines: list[str] = []
    lines.append("# Autonomous Review Verdicts")
    lines.append("")
    lines.append("| # | Title | Steps | Reproduced | Crash | Verdict |")
    lines.append("|---|-------|-------|------------|-------|---------|")
    for r in results:
        num = r.issue_url.rstrip("/").rsplit("/", 1)[1]
        title_short = r.issue_title[:50]
        steps_ran = sum(1 for s in r.steps_executed if s.get("ran"))
        steps_total = len(r.steps_executed)
        reproduced = "✅" if r.reproduced else "❌"
        crash = "💥 YES" if r.crash_observed else "no"
        all_tty = r.steps_executed and all(s.get("tty_error") for s in r.steps_executed)
        if all_tty:
            verdict_short = "⚠ INCONCLUSIVE"
        elif r.crash_observed:
            verdict_short = "CONFIRMED"
        elif r.reproduced:
            verdict_short = "OK"
        else:
            verdict_short = "FAIL"
        lines.append(
            f"| [{num}](#{num}) | {title_short} | {steps_ran}/{steps_total}"
            f" | {reproduced} | {crash} | {verdict_short} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    for r in results:
        num = r.issue_url.rstrip("/").rsplit("/", 1)[1]
        lines.append(f"## #{num} — {r.issue_title}")
        lines.append(f"> <{r.issue_url}>")
        lines.append("")
        lines.append(f"**Verdict:** {r.verdict}")
        lines.append("")

        if r.errors:
            lines.append("**⚠ Errors during review:**")
            for e in r.errors:
                lines.append(f"- `{e}`")
            lines.append("")

        if r.steps_executed:
            lines.append(f"**Steps executed: {len(r.steps_executed)}**")
            lines.append("")
            for step in r.steps_executed:
                status = (
                    "💥 CRASHED" if step.get("crashed")
                    else ("❓ INCONCLUSIVE" if step.get("tty_error")
                    else ("✅ OK" if step.get("ran") else "⏭ SKIPPED"))
                )
                exit_code = step.get("exit_code", "—")
                lines.append(
                    f"### Step {step['step']} {status} — {step['description']}"
                )
                lines.append(f"- **Command:** `{step.get('command', '—')}`")
                lines.append(f"- **Exit code:** `{exit_code}`")
                if step.get("error"):
                    lines.append(f"- **Error:** {step['error']}")
                stdout = step.get("stdout", "").strip()
                stderr = step.get("stderr", "").strip()
                if stdout:
                    lines.append("")
                    lines.append("**stdout:**")
                    lines.append("```")
                    lines.append(stdout[:600])
                    lines.append("```")
                if stderr:
                    lines.append("")
                    lines.append("**stderr:**")
                    lines.append("```")
                    lines.append(stderr[:600])
                    lines.append("```")
                if step.get("core_dump"):
                    lines.append("")
                    lines.append("**Core dump:**")
                    lines.append("```")
                    lines.append(step["core_dump"][:400])
                    lines.append("```")
                lines.append("")

        lines.append("---")
        lines.append("")

    path.write_text("\n".join(lines))


def _print_summary(results: list[ReviewResult]) -> None:
    print()
    crashes = sum(1 for r in results if r.crash_observed)
    failures = sum(1 for r in results if not r.success)
    print("=" * 60)
    print(f"Total:     {len(results)}")
    print(f"Crashes:   {crashes} {'💥' if crashes else ''}")
    print(f"Failures:  {failures}")
    print(f"Verdicts:  {Path('verdicts.md').resolve()}")
    print()
