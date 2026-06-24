# Using the CLI

`communiy-manager` ships with two subcommands: `fetch` and `review`.

---

## Prerequisites

- Python ≥ 3.14
- [uv](https://docs.astral.sh/uv/)

Clone and sync:

```bash
git clone ssh://git@codeberg.org/gbrennon/community-manager.git
cd community-manager
uv sync
```

---

## Fetch an issue

```bash
uv run communiy-manager fetch https://github.com/cline/cline/issues/11761
# or the short form (bare URL):
uv run communiy-manager https://github.com/cline/cline/issues/11761
```

Output:

```
Title: core dumped when trying to exit
State: Open
---
### Cline Surface
CLI
...
```

---

## Review an issue autonomously

```bash
uv run communiy-manager review https://github.com/cline/cline/issues/11761
```

This drives the **full autonomous pipeline**:

1. Fetches the issue from GitHub
2. Parses "Steps to reproduce" from the body
3. Launches an isolated Docker container (`--network none`)
4. Runs `cline` inside following each step
5. Detects crashes (SIGSEGV, SIGABRT, core dumps)
6. Destroys the sandbox (always, even on errors)
7. Prints a verdict and writes `findings.md`

```
Reviewing https://github.com/cline/cline/issues/11761 ...

============================================================
Title:       core dumped when trying to exit
Sandbox:     cline-issue-a1b2c3d4e5f6
Reproduced:  True
Crash:       True

VERDICT:
**CONFIRMED**: Crash reproduced inside the sandbox. Cline exits
with core dump after SIGINT/Ctrl+C. Likely a bun-level or
Cline signal-handling bug.

Report written to findings.md
```

### Options

```bash
# Use QEMU instead of Docker
uv run communiy-manager review --provider qemu https://github.com/cline/cline/issues/11761

# Custom report path
uv run communiy-manager review --out /tmp/report.md https://github.com/cline/cline/issues/11761
```

---

## Help

```bash
uv run communiy-manager --help
uv run communiy-manager fetch --help
uv run communiy-manager review --help
```

---

## Error handling

If the URL is invalid or the API request fails, the CLI prints the error to
stderr and exits with code 1:

```bash
$ uv run communiy-manager fetch https://github.com/nonexistent/repo/issues/1
Error: HTTP Error 404: Not Found
```

---

## Programmatic API

The `run()` function accepts an optional `fetcher` argument for dependency
injection (used in tests):

```python
from community_manager.cli import run
from community_manager.fetcher import GitHubIssueFetcher

run(["fetch", "https://github.com/owner/repo/issues/1"],
    fetcher=GitHubIssueFetcher())
```

For the full sandbox review API, import directly:

```python
import asyncio
from community_manager.sandbox.reviewer import IssueReviewer

async def main():
    reviewer = IssueReviewer()
    result = await reviewer.review(
        "https://github.com/cline/cline/issues/11761"
    )
    print(result.verdict)

asyncio.run(main())
```
