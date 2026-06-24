# Using the CLI

`communiy-manager` ships with a CLI that fetches public GitHub issues and
displays them.

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

## Help

```bash
uv run communiy-manager --help
```

```
usage: communiy-manager [-h] url

Fetch public GitHub issues and display them.

positional arguments:
  url         GitHub issue URL, e.g. https://github.com/cline/cline/issues/11761
```

---

## Error handling

If the URL is invalid or the API request fails, the CLI prints the error to
stderr and exits with code 1:

```bash
$ uv run communiy-manager https://github.com/nonexistent/repo/issues/1
Error: HTTP Error 404: Not Found
```

---

## API

The `run()` function accepts an optional `fetcher` argument for dependency
injection (used in tests):

```python
from community_manager.cli import run
from community_manager.fetcher import GitHubIssueFetcher

run(["https://github.com/owner/repo/issues/1"], fetcher=GitHubIssueFetcher())
```
