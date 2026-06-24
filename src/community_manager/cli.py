from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Sequence

from community_manager.fetcher import GitHubIssueFetcher
from community_manager.sandbox.reviewer import IssueReviewer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="communiy-manager",
        description="Fetch and autonomously review Cline GitHub issues.",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    # fetch — display an issue
    fetch_cmd = sub.add_parser("fetch", help="Fetch and display a GitHub issue")
    fetch_cmd.add_argument(
        "url",
        help="GitHub issue URL, e.g. https://github.com/cline/cline/issues/11761",
    )

    # review — full autonomous pipeline (default when a URL is given)
    review_cmd = sub.add_parser(
        "review",
        help="Autonomously review a GitHub issue inside a sandbox",
    )
    review_cmd.add_argument(
        "url",
        help="GitHub issue URL",
    )
    review_cmd.add_argument(
        "--provider",
        choices=["docker", "qemu"],
        default="docker",
        help="Sandbox backend (default: docker)",
    )
    review_cmd.add_argument(
        "--out",
        default="findings.md",
        help="Report output path (default: findings.md)",
    )

    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
    parser = build_parser()

    # Backward-compat: if only a URL is passed (no subcommand), treat
    # it as the old `fetch` behaviour.
    if argv is None:
        argv = sys.argv[1:]

    # Fast path: single positional URL → redirect to `fetch`
    if argv and not argv[0].startswith("-") and argv[0] not in {"fetch", "review"}:
        argv = ["fetch"] + list(argv)

    args = parser.parse_args(argv)

    if args.command == "fetch" or (args.command is None and hasattr(args, "url")):
        _run_fetch(args.url, fetcher=fetcher)
    elif args.command == "review":
        _run_review(
            args.url,
            provider=args.provider,
            out=args.out,
            fetcher=fetcher,
        )
    else:
        parser.print_help()


# ------------------------------------------------------------------
# implementations
# ------------------------------------------------------------------

def _run_fetch(
    url: str,
    *,
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
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
    provider: str = "docker",
    out: str = "findings.md",
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
    """Autonomously review a Cline issue end-to-end."""
    if provider == "qemu":
        from community_manager.sandbox.qemu_provider import QemuProvider
        from community_manager.sandbox.protocol import SandboxConfig
        prov: object = QemuProvider(config=SandboxConfig())
    else:
        from community_manager.sandbox.docker_provider import DockerProvider
        from community_manager.sandbox.protocol import SandboxConfig
        prov = DockerProvider(config=SandboxConfig())

    reviewer = IssueReviewer(
        provider=prov,
        fetcher=fetcher or GitHubIssueFetcher(),
    )

    async def _do() -> None:
        print(f"Reviewing {url} ...")
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
