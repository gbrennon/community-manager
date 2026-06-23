from __future__ import annotations

import argparse
import sys
from typing import Sequence

from community_manager.fetcher import GitHubIssueFetcher


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="communiy-manager",
        description="Fetch public GitHub issues and display them.",
    )
    parser.add_argument(
        "url",
        help="GitHub issue URL, e.g. https://github.com/cline/cline/issues/11761",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    fetcher: GitHubIssueFetcher | None = None,
) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if fetcher is None:
        fetcher = GitHubIssueFetcher()
    try:
        issue = fetcher.fetch(args.url)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Title:", issue.title)
    print("State:", issue.state.value)
    print("---")
    print(issue.body or "(empty body)")
