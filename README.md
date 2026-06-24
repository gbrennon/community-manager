# community-manager

Autonomous Cline issue triage — fetch, sandbox, reproduce, verdict.

Given a GitHub issue URL, `community-manager` fetches the issue body,
parses its "Steps to reproduce", launches an **isolated, network‑locked
sandbox** (Docker container or QEMU VM), runs `cline` inside following
each step, captures crash signals and core dumps, destroys the sandbox,
and writes a structured review report with a verdict.

**Project:** https://codeberg.org/gbrennon/community-manager

---

## Quick start

```bash
git clone ssh://git@codeberg.org/gbrennon/community-manager.git
cd community-manager
uv sync
uv run pytest       # 45 tests, 74 % coverage
```

---

## CLI

```bash
# Fetch & display a GitHub issue (also works as bare URL)
uv run communiy-manager fetch https://github.com/cline/cline/issues/11761

# Autonomously review — fetch, sandbox, reproduce, verdict
uv run communiy-manager review https://github.com/cline/cline/issues/11761
```

For the full CLI reference see **[docs/using-the-cli.md](docs/using-the-cli.md)**.

---

## Autonomous sandbox review

The heart of the project is the sandbox subsystem.

```python
import asyncio
from community_manager.sandbox.reviewer import IssueReviewer

async def main():
    reviewer = IssueReviewer()
    result = await reviewer.review(
        "https://github.com/cline/cline/issues/11761"
    )
    print(f"Crash observed: {result.crash_observed}")
    print(f"Verdict: {result.verdict}")
    reviewer.write_report(result)  # → findings.md

asyncio.run(main())
```

See **[docs/sandbox-review.md](docs/sandbox-review.md)** for the full walkthrough.

---

## Architecture

```
GitHubIssueFetcher          ← fetches issues from GitHub REST API

SandboxProvider (ABC)       ← protocol
  ├── DockerProvider        ← docker run --network none
  └── QemuProvider          ← qemu-system-x86_64 -net none

IssueReviewer               ← orchestrator
  ├── .review(url)          ← single-issue pipeline
  └── .review_many([urls])  ← concurrent, N sandboxes
```

---

## Project structure

```
community-manager/
├── src/community_manager/
│   ├── __init__.py              # entry point
│   ├── cli.py                   # argument parser + run()
│   ├── fetcher.py               # GitHubIssueFetcher
│   ├── issue.py                 # Issue dataclass
│   ├── issue_state.py           # IssueState StrEnum
│   └── sandbox/
│       ├── __init__.py          # public API
│       ├── protocol.py          # SandboxProvider ABC
│       ├── docker_provider.py   # Docker backend
│       ├── qemu_provider.py     # QEMU backend
│       └── reviewer.py          # IssueReviewer orchestrator
├── tests/
│   ├── community_manager/       # unit + integration tests
│   └── cassettes/               # VCR cassettes
├── docs/                        # usage & development guides
├── pyproject.toml
└── README.md
```

---

## Development

```bash
uv sync                  # install dependencies
uv run pytest -v         # 45 tests
uv run ruff check .      # lint (if ruff is installed)
uv run ruff format .     # format (if ruff is installed)
```

Conventional commits (one commit per file):

```
feat(sandbox): add SandboxProvider protocol
feat(sandbox): add DockerProvider implementation
feat(sandbox): add QemuProvider implementation
feat(sandbox): add IssueReviewer orchestrator with verdicts
test(sandbox): add 15 tests for IssueReviewer + crash simulation
```

See **[docs/contributing.md](docs/contributing.md)** for details.

---

## License

TBD
