# CLI reference

Three subcommands: `fetch`, `review`, `batch`.

---

## `fetch` — display an issue

```bash
uv run communiy-manager fetch https://github.com/cline/cline/issues/11761
# or the shorthand (bare URL):
uv run communiy-manager https://github.com/cline/cline/issues/11761
```

| Argument | Description |
|----------|-------------|
| `url`    | Full GitHub issue URL |

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

## `review` — autonomous single‑issue pipeline

```bash
uv run communiy-manager review https://github.com/cline/cline/issues/11761
```

| Flag | Default | Description |
|------|---------|-------------|
| `url` | *(required)* | GitHub issue URL |
| `--debug` | off | Print every action as it happens |
| `--provider` | `auto` | `container`, `qemu`, or `auto` (detect) |
| `--container-runtime` | `auto` | `docker`, `podman`, or `auto` (detect) |
| `--out` | `findings.md` | Report output path |

### What it does

1. Fetches the issue from GitHub REST API
2. Extracts "Steps to reproduce" and the `cline` version from the body
3. Launches an ephemeral container (`node:22-slim`)
4. Runs `npm install -g cline@<VERSION>` — network is **enabled** during install
5. **Severs the network** — the container has no internet during reproduction
6. Maps each human step to a `cline` shell command and executes it
7. Detects crashes (SIGSEGV 139, SIGABRT 134, core dumps)
8. Destroys the container **always** (even on error)
9. Prints a verdict and writes `findings.md`

### Debug output

```bash
uv run communiy-manager review --debug https://github.com/cline/cline/issues/11761
```

Shows every internal action:

```
[debug] Fetching https://github.com/cline/cline/issues/11761
[debug] Issue: core dumped when trying to exit  cline_version='3.0.29'
[debug] Steps parsed: 3 ['open cline', 'press ctrl+c', 'raises core dumped error']
[debug] Launching sandbox...
[debug] Sandbox launched: cline-issue-799f8070c9d7
[debug] Installing cline@3.0.29 (this may take ~30s)...
[debug] cline@3.0.29 installed
[debug] Network: disconnecting...
[debug] Network: severed
[debug] Step 1: ['sh', '-c', 'timeout 5 cline']
[debug]   exit=0 stdout=...
[debug] Step 2: ['sh', '-c', 'cline & P=$!; sleep 2; kill -2 $P; ...']
[debug]   exit=143 stdout=EXIT:143
[debug] Destroying sandbox cline-issue-799f8070c9d7
[debug] Sandbox destroyed
[debug] Report written to findings.md
```

---

## `batch` — review a range of issues

```bash
uv run communiy-manager batch cline/cline 11760 11765
```

| Argument/Flag | Default | Description |
|---------------|---------|-------------|
| `repo` | *(required)* | GitHub owner/repo, e.g. `cline/cline` |
| `start` | *(required)* | First issue number |
| `end` | *(required)* | Last issue number (inclusive) |
| `--debug` | off | Print every action as it happens |
| `--provider` | `auto` | `container`, `qemu`, or `auto` |
| `--container-runtime` | `auto` | `docker`, `podman`, or `auto` |
| `--concurrent` | `4` | Max parallel reviews |
| `--out-dir` | `reports` | Directory for per‑issue markdown reports |

### What it does

Each issue gets its **own ephemeral sandbox** — no cross‑contamination.

1. Generates URLs from `start` to `end` (e.g. `11760` → `11765`)
2. Runs reviews concurrently (default 4 at a time)
3. Writes per‑issue reports to `--out-dir` (`reports/11760.md`, …)
4. Prints a summary table

```
$ uv run communiy-manager batch cline/cline 11760 11765 --concurrent 2
Batch reviewing 6 issues (cline/cline #11760–#11765) ...
Sandbox: podman | concurrent: 2
  #11760  some issue title                                       ok
  #11761  core dumped when trying to exit                        ok
  #11762  another issue title                                    FAIL
  #11763  crash on startup                                       CONFIRMED
  #11764  feature request                                        ok
  #11765  docs typo                                              ok

Done. 6 reviews, 1 crashes confirmed. Reports in reports/
```

Each report is a full markdown file with:
- Issue metadata (title, URL, body excerpt)
- Reproduction summary (steps, crash detected)
- Per‑step breakdown with exit codes, stdout/stderr, core dumps
- Human‑readable verdict

---

## Auto‑detection

The CLI auto‑detects what's installed:

| Flag | `auto` behaviour |
|------|------------------|
| `--provider` | Prefers container (podman > docker), falls back to qemu |
| `--container-runtime` | Prefers podman, then docker, then errors |

Errors are printed clearly when a required binary or disk image is missing.

---

## Verdict types

| Condition | Verdict |
|-----------|---------|
| All steps ran, crash signal detected | **CONFIRMED**: Crash reproduced inside the sandbox |
| All steps ran, no crash | Issue reproduced but no crash observed in this environment |
| Steps failed to run | Cannot reproduce — all steps ran without crash |
