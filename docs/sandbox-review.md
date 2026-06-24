# Autonomous sandbox review

The sandbox subsystem lets you triage GitHub issues inside **isolated,
network‑locked containers** — no host exposure, no side effects.

---

## Why sandbox the review?

When a user files an issue containing repro scripts, suspicious links, or
arbitrary code, you don't want it anywhere near your host machine.
community‑manager spins up a **throw‑away Docker container or QEMU VM**,
reproduces the issue inside it, captures the results, and tears everything
down.

| Risk               | Sandbox protection                               |
|--------------------|--------------------------------------------------|
| Malicious payload  | Executes inside the throw‑away guest             |
| Credential leaks   | No NIC = nothing can phone home                  |
| Filesystem damage  | Guest filesystem dies with the sandbox           |
| Resource abuse     | CPU / RAM caps per container                     |

---

## Quick start

```python
import asyncio
from community_manager.sandbox.reviewer import IssueReviewer

async def main():
    reviewer = IssueReviewer()
    result = await reviewer.review(
        "https://github.com/cline/cline/issues/11761"
    )
    print(f"Title:       {result.issue_title}")
    print(f"Crash:       {result.crash_observed}")
    print(f"Reproduced:  {result.reproduced}")
    print(f"Verdict:     {result.verdict}")

    reviewer.write_report(result)  # writes findings.md

asyncio.run(main())
```

---

## What happens under the hood

```
review(url)
  ├─► GitHubIssueFetcher.fetch()       ← real GitHub API
  ├─► DockerProvider.launch()          ← docker run --network none
  ├─► DockerProvider.copy_in()         ← cp project → /home/cline/project
  ├─► parse "Steps to reproduce"
  ├─► for each step:
  │     _step_to_cline_cmd() → ["bash", "-c", "cline & P=$!; ..."]
  │     DockerProvider.exec()
  │     _is_crash()           → detects SIGSEGV, core dumps
  ├─► _build_verdict()                ← human‑readable verdict
  └─► DockerProvider.destroy()        ← guaranteed (finally block)
```

---

## Configuration

```python
from community_manager.sandbox.protocol import SandboxConfig

config = SandboxConfig(
    memory="4g",           # Docker --memory
    cpus=4,                # Docker --cpus
    network_enabled=False, # --network none when False
    workspace_dir=Path("/tmp/review"),
)
```

---

## Choosing a provider

```python
from community_manager.sandbox.docker_provider import DockerProvider
from community_manager.sandbox.qemu_provider import QemuProvider

# Docker (default) — lighter, faster, requires Docker daemon
reviewer = IssueReviewer(provider=DockerProvider(config=config))

# QEMU — full‑VM isolation, requires cline-review.qcow2 disk image
reviewer = IssueReviewer(provider=QemuProvider(config=config))
```

---

## Concurrent reviews

Each issue gets its own sandbox.  No cross‑contamination.

```python
urls = [
    "https://github.com/cline/cline/issues/11761",
    "https://github.com/cline/cline/issues/11762",
    "https://github.com/cline/cline/issues/11763",
]
results = await reviewer.review_many(urls, max_concurrent=4)

for r in results:
    print(r.issue_title, r.verdict)
```

---

## Crash detection

The reviewer recognises crashes via:

| Signal         | Exit code | Detection                             |
|----------------|-----------|---------------------------------------|
| SIGABRT        | 134       | `res.exit_code == 134`                |
| SIGSEGV        | 139       | `res.exit_code == 139`                |
| Timeout        | 124       | `res.exit_code == 124`                |
| SIGKILL        | 137       | `res.exit_code == 137`                |
| Core dump text | any       | `"core dumped" in stdout or stderr`   |

When a crash is detected, the reviewer runs `coredumpctl list` inside the
sandbox to capture core dump metadata.

---

## Report output

`findings.md` contains:

- Issue metadata (title, URL, body excerpt)
- Reproduction summary (steps parsed, all‑ran, crash detected)
- Per‑step breakdown with exit codes, stdout/stderr, core dumps
- Human‑readable verdict

Example verdict:

> **CONFIRMED**: Crash reproduced inside the sandbox. Cline exits with core
dump after SIGINT/Ctrl+C. Likely a bun‑level or Cline signal‑handling bug.

---

## The Protocol

Every sandbox backend implements `SandboxProvider`:

```python
class SandboxProvider(ABC):
    async def launch(self) -> str:                    ...
    async def copy_in(self, sid, host, sandbox):       ...
    async def exec(self, sid, command) -> SandboxResult: ...
    async def destroy(self, sid):                      ...
    async def is_healthy(self, sid) -> bool:            ...
```

This means any backend (Docker, Podman, QEMU, Firecracker, LXC) works as
long as it fulfils the interface.

---

## Dockerfile for the sandbox image

The Docker provider expects an image named `cline-review-sandbox`:

```dockerfile
FROM node:22-slim
RUN useradd --create-home cline
USER cline
WORKDIR /home/cline
RUN npm install -g typescript tsx vitest
CMD ["/bin/bash"]
```

Build it once:

```bash
docker build -t cline-review-sandbox .
```

---

## Verdict types

| Condition                        | Verdict                                            |
|----------------------------------|----------------------------------------------------|
| All steps ran, crash detected    | **CONFIRMED**: Crash reproduced inside the sandbox |
| All steps ran, no crash          | Issue reproduced but no crash observed             |
| Steps failed to run              | Cannot reproduce — all steps ran without crash     |
