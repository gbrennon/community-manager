# Contributing

Conventions, commit style, and development workflow for community‑manager.

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

## Running tests

```bash
uv run pytest -v          # full suite (45 tests)
uv run pytest -v tests/community_manager/test_sandbox.py   # sandbox only
```

Coverage reports are generated automatically (config in `pyproject.toml`).

---

## Running a single test

```bash
uv run pytest tests/community_manager/test_sandbox.py::TestReviewRealIssueCrashSim::test_parses_and_detects_crash -v
```

---

## Lint and format

```bash
uv run ruff check .       # lint
uv run ruff format .      # format
```

(Install ruff via `uv add --dev ruff` if not present.)

---

## Commit conventions

One commit per logical file / feature.  Use the format:

```
<type>(<scope>): <description>

<optional body>
```

| Type     | When                                   |
|----------|----------------------------------------|
| `feat`   | New feature (protocol, provider, etc)  |
| `fix`    | Bug fix                                |
| `test`   | Adding or updating tests               |
| `chore`  | Tooling, .gitignore, deps              |
| `docs`   | Documentation only                     |

Examples from this repo:

```
feat(sandbox): add SandboxProvider protocol
feat(sandbox): add DockerProvider implementation
feat(sandbox): add QemuProvider implementation
feat(sandbox): add IssueReviewer orchestrator with verdicts
test(sandbox): add 15 tests for IssueReviewer + crash simulation
chore: add findings.md and coverage artifacts to .gitignore
```

---

## Project structure

```
src/community_manager/
├── __init__.py              # entry point → main()
├── cli.py                   # argparse + run()
├── fetcher.py               # GitHubIssueFetcher
├── issue.py                 # Issue dataclass
├── issue_state.py           # IssueState StrEnum
└── sandbox/
    ├── __init__.py          # public API
    ├── protocol.py          # SandboxProvider ABC
    ├── docker_provider.py   # Docker backend
    ├── qemu_provider.py     # QEMU backend
    └── reviewer.py          # IssueReviewer orchestrator
```

---

## Adding a new sandbox provider

1. Create `src/community_manager/sandbox/my_provider.py`
2. Subclass `SandboxProvider` and implement all abstract methods
3. Add the provider to `sandbox/__init__.py` exports
4. Write tests using a fake provider or mocking subprocess
5. Commit as `feat(sandbox): add MyProvider implementation`

---

## Test patterns

- **`FakeGitHubIssueFetcher`** — injects canned API responses, no network
- **`CrashSimProvider`** — returns SIGSEGV 139 on ctrl+c steps
- **`HappyProvider`** — always succeeds (exit_code=0)
- **`VCR cassettes`** — replay real API responses for integration tests

See `tests/conftest.py` and `tests/community_manager/test_sandbox.py` for
examples.

---

## Pull request checklist

- [ ] All tests pass (`uv run pytest -q`)
- [ ] New code has test coverage
- [ ] Conventional commit format (one file per commit)
- [ ] No generated files committed (findings.md, htmlcov/, .coverage)
- [ ] README and docs updated if needed
