"""Orchestrates autonomous Cline issue review inside an isolated sandbox."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from community_manager.fetcher import GitHubIssueFetcher
from community_manager.sandbox.protocol import SandboxConfig, SandboxProvider
from community_manager.sandbox.docker_provider import DockerProvider

_CRASH_EXIT_CODES = frozenset({134, 139, 124, 137})
_CRASH_TEXT_MARKERS = ("core dumped", "segmentation fault", "signal")
# Markers that indicate cline couldn't start at all — sandbox env issue, not the bug.
_TTY_ERROR_MARKERS = (
    "interactive mode requires a tty",
    "requires a tty",
    "not a tty",
    "no tty",
)
_FALLBACK_STEPS = ("1. open cline", "2. press ctrl+c", "3. observe crash")
_STEP_BODY_REGEX = re.compile(
    r"### Steps to reproduce\s*\n((?:\d+\.\s*.+\n?)+)", re.IGNORECASE,
)
_STEP_NUMBER_PREFIX = re.compile(r"^\d+\.\s*")

VERDICT_CONFIRMED = (
    "**CONFIRMED**: Crash reproduced inside the sandbox. "
    "Cline exits with core dump after SIGINT/Ctrl+C. "
    "Likely a bun-level or Cline signal-handling bug."
)
VERDICT_NOT_REPRODUCED = "Cannot reproduce — all steps ran without crash."
VERDICT_NO_CRASH = "Issue reproduced but no crash observed in this environment."
VERDICT_INCONCLUSIVE = (
    "**INCONCLUSIVE**: cline could not start inside the sandbox "
    "(TTY/terminal error). The sandbox environment cannot reproduce "
    "this issue class — manual verification required."
)


@dataclass
class ReviewResult:
    issue_url: str
    issue_title: str
    sandbox_id: str
    success: bool
    reproduced: bool = False
    crash_observed: bool = False
    verdict: str = ""
    findings: str = ""
    steps_executed: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class IssueReviewer:

    def __init__(
        self,
        provider: SandboxProvider | None = None,
        config: SandboxConfig | None = None,
        fetcher: GitHubIssueFetcher | None = None,
        debug: bool = False,
    ) -> None:
        self.config = config or SandboxConfig()
        self.provider = provider or DockerProvider(config=self.config)
        self.fetcher = fetcher or GitHubIssueFetcher()
        self.debug = debug

    def _log(self, *args: Any) -> None:
        if self.debug:
            print("[debug]", *args, flush=True)

    async def review(
        self, issue_url: str, *, project_dir: Path | None = None,
    ) -> ReviewResult:
        self._log(f"Fetching {issue_url}")
        issue = self._fetch_issue_or_error(issue_url)
        if isinstance(issue, ReviewResult):
            return issue

        self._log(f"Issue: {issue.title}  cline_version={issue.cline_version!r}")

        reproduction_steps = parse_steps_from_issue_body(issue.body) or list(_FALLBACK_STEPS)
        self._log(f"Steps parsed: {len(reproduction_steps)}", reproduction_steps)

        self._log("Launching sandbox...")
        sandbox_id = await self.provider.launch(cline_version=issue.cline_version)
        self._log(f"Sandbox launched: {sandbox_id}")

        from_cache = getattr(self.provider, "launched_from_cache", False)
        if from_cache:
            self._log(f"Cache hit — skipping npm install for cline@{issue.cline_version}")

        result = ReviewResult(
            issue_url=issue_url, issue_title=issue.title,
            sandbox_id=sandbox_id, success=False,
        )
        try:
            if not from_cache:
                await self._install_cline(sandbox_id, issue.cline_version)
            self._log("Network: disconnecting...")
            await self.provider.disconnect_network(sandbox_id)
            self._log("Network: severed")

            reproduced, crash_detected, step_log = await self._execute_steps(
                sandbox_id, reproduction_steps,
            )
            result.reproduced = reproduced
            result.crash_observed = crash_detected
            result.steps_executed = step_log
            result.findings = render_findings(issue.title, issue.body, result)
            result.verdict = render_verdict(result)
            result.success = True
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            result.errors.append(str(exc))
        finally:
            self._log(f"Destroying sandbox {sandbox_id}")
            await self.provider.destroy(sandbox_id)
            self._log("Sandbox destroyed")
        return result
    async def review_many(
        self, urls: list[str], *, project_dir: Path | None = None,
        max_concurrent: int = 4,
    ) -> list[ReviewResult]:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def review_one(url: str) -> ReviewResult:
            async with semaphore:
                provider_cls = type(self.provider)
                if isinstance(self.provider, DockerProvider):
                    fresh_provider = provider_cls(config=self.config, binary=self.provider.binary)
                else:
                    fresh_provider = provider_cls(config=self.config)
                reviewer = IssueReviewer(
                    provider=fresh_provider, config=self.config, fetcher=self.fetcher,
                    debug=self.debug,
                )
                return await reviewer.review(url, project_dir=project_dir)

        return await asyncio.gather(*(review_one(u) for u in urls))

    def _fetch_issue_or_error(self, url: str) -> Any:
        try:
            return self.fetcher.fetch(url)
        except Exception as exc:
            return ReviewResult(
                issue_url=url, issue_title="(fetch error)",
                sandbox_id="", success=False,
                errors=[f"Failed to fetch issue: {exc}"],
            )

    async def _install_cline(self, sandbox_id: str, cline_version: str) -> None:
        package = f"cline@{cline_version}" if cline_version else "cline"
        # cline bundles bun (~90 MB) + its own JS (~60 MB): real-world installs
        # take 2–5 min on a cold node:22-slim image depending on npm registry speed.
        self._log(f"Installing {package} (expected: 2–5 min, cline bundles bun ~150 MB)...")
        result = await self._npm_install_streaming(sandbox_id, package)
        if result.exit_code != 0 and "ETARGET" in result.stdout and cline_version:
            self._log(f"Version {cline_version} not found on registry, falling back to latest")
            package = "cline"
            result = await self._npm_install_streaming(sandbox_id, package)
        if result.exit_code != 0:
            raise RuntimeError(f"npm install -g {package} failed:\n{result.stdout[-800:]}")
        self._log(f"{package} installed successfully")
        await self._commit_image(sandbox_id, cline_version)

    async def _commit_image(self, sandbox_id: str, cline_version: str) -> None:
        """Snapshot the container into a reusable cached image if possible."""
        from community_manager.sandbox.docker_provider import DockerProvider
        if not isinstance(self.provider, DockerProvider):
            return
        if self.provider.image_cache is None:
            return
        self._log(f"Committing image for cline@{cline_version} ...")
        await self.provider.image_cache.commit(sandbox_id, cline_version)

    async def _npm_install_streaming(self, sandbox_id: str, package: str) -> Any:
        from community_manager.sandbox.docker_provider import DockerProvider

        command = ["npm", "install", "-g", "--foreground-scripts", package]

        if not isinstance(self.provider, DockerProvider):
            # non-Docker providers: fall back to buffered exec
            return await self.provider.exec(sandbox_id, command)

        def _on_line(line: str) -> None:
            # surface npm progress lines (added, progress, warn, error) and
            # any non-empty line while in debug mode
            low = line.lower()
            important = any(tok in low for tok in ("added", "warn", "err!", "error", "npm"))
            if self.debug and (important or line.strip()):
                self._log(f"  npm › {line}")

        def _on_tick(elapsed: float) -> None:
            mins = int(elapsed) // 60
            secs = int(elapsed) % 60
            self._log(
                f"  npm still running … {mins}m{secs:02d}s elapsed"
                f" (container {sandbox_id[:20]})"
            )

        return await self.provider.exec_streaming(
            sandbox_id,
            command,
            on_line=_on_line,
            tick_interval=15.0,
            on_tick=_on_tick,
        )

    async def _execute_steps(
        self, sandbox_id: str, steps: list[str],
    ) -> tuple[bool, bool, list[dict[str, Any]]]:
        step_log: list[dict[str, Any]] = []
        any_crash = False
        every_step_ran = True

        for index, step_description in enumerate(steps, start=1):
            command = convert_step_to_cline_command(step_description)
            record: dict[str, Any] = {
                "step": index, "description": step_description,
                "command": " ".join(command), "ran": False,
            }
            self._log(f"Step {index}: {command}")
            try:
                result = await self.provider.exec(sandbox_id, command, tty=True)
                record["exit_code"] = result.exit_code
                record["stdout"] = result.stdout[-2000:]
                record["stderr"] = result.stderr[-2000:]
                self._log(f"  exit={result.exit_code} stdout={result.stdout[:100]}")

                combined = (result.stdout + result.stderr).lower()
                if any(m in combined for m in _TTY_ERROR_MARKERS):
                    record["tty_error"] = True
                    record["ran"] = False
                    every_step_ran = False
                    self._log("  <<< INCONCLUSIVE: cline could not start (TTY error) >>>")
                else:
                    record["ran"] = True

                if process_exited_with_crash(result):
                    any_crash = True
                    record["crashed"] = True
                    self._log("  <<< CRASH DETECTED >>>")
                    core_dump = await self.provider.exec(
                        sandbox_id,
                        ["sh", "-c", "coredumpctl list 2>/dev/null || echo none"],
                    )
                    record["core_dump"] = core_dump.stdout[:1000]
                    self._log(f"  core_dump: {core_dump.stdout[:100]}")
            except Exception as exc:
                record["error"] = str(exc)
                every_step_ran = False
            step_log.append(record)

        return every_step_ran, any_crash, step_log

    def write_report(self, result: ReviewResult, output_path: Path | None = None) -> Path:
        path = output_path or Path("findings.md")
        path.write_text(render_markdown_report(result))
        self._log(f"Report written to {path}")
        return path


def parse_steps_from_issue_body(body: str) -> list[str]:
    matched = _STEP_BODY_REGEX.search(body)
    if not matched:
        return []
    return [
        _STEP_NUMBER_PREFIX.sub("", line).strip()
        for line in matched.group(1).strip().split("\n")
        if line.strip()
    ]


def convert_step_to_cline_command(step: str) -> list[str]:
    lowered = step.lower()
    if "version" in lowered:
        return ["sh", "-c", "cline --version"]
    if "open" in lowered and "cline" in lowered:
        return ["sh", "-c", "timeout 5 cline"]
    if any(signal in lowered for signal in ("ctrl+c", "sigint", "sigkill")):
        return [
            "sh", "-c",
            "cline & P=$!; sleep 2; kill -2 $P; wait $P 2>/dev/null; echo EXIT:$?",
        ]
    if "exit" in lowered:
        return ["sh", "-c", "echo exit | timeout 3 cline || true"]
    return ["sh", "-c", "timeout 10 cline"]


def process_exited_with_crash(result: Any) -> bool:
    if result.exit_code == 0:
        return False
    if result.exit_code in _CRASH_EXIT_CODES:
        return True
    combined_output = (result.stdout + result.stderr).lower()
    return any(marker in combined_output for marker in _CRASH_TEXT_MARKERS)


def render_verdict(result: ReviewResult) -> str:
    if result.steps_executed and all(s.get("tty_error") for s in result.steps_executed):
        return VERDICT_INCONCLUSIVE
    if not result.reproduced:
        return VERDICT_NOT_REPRODUCED
    if result.crash_observed:
        return VERDICT_CONFIRMED
    return VERDICT_NO_CRASH


def render_findings(title: str, body: str, result: ReviewResult) -> str:
    lines: list[str] = []
    lines.append("## Issue")
    lines.append(f"**Title:** {title}")
    lines.append(f"**Body excerpt:** {body[:200]}...")
    lines.append("")
    lines.append("## Reproduction")
    lines.append(f"- Steps executed: {len(result.steps_executed)}")
    lines.append(f"- All steps ran: {result.reproduced}")
    lines.append(f"- Crash detected: {result.crash_observed}")
    for record in result.steps_executed:
        status = _step_status_label(record)
        exit_code = record.get("exit_code", "—")
        lines.append(f"\n### Step {record['step']} [{status}]")
        lines.append(f"**Description:** {record['description']}")
        lines.append(f"**Command:** `{record.get('command', '—')}`")
        lines.append(f"**Exit code:** `{exit_code}`")
        if record.get("error"):
            lines.append(f"**Error:** {record['error']}")
        for stream in ("stdout", "stderr"):
            output = record.get(stream, "").strip()
            if output:
                lines.append(f"**{stream}:**")
                lines.append("```")
                lines.append(output[:500])
                lines.append("```")
        if record.get("core_dump"):
            lines.append("**Core dump:**")
            lines.append("```")
            lines.append(record["core_dump"][:400])
            lines.append("```")
    return "\n".join(lines)


def _step_status_label(record: dict[str, Any]) -> str:
    if record.get("crashed"):
        return "CRASHED"
    if record.get("tty_error"):
        return "INCONCLUSIVE (no TTY)"
    if record["ran"]:
        return "OK"
    return "SKIPPED"


def render_markdown_report(result: ReviewResult) -> str:
    lines = [
        "# Cline Issue Review Report", "",
        f"- **Issue:** {result.issue_url}",
        f"- **Title:** {result.issue_title}",
        f"- **Sandbox:** `{result.sandbox_id}`",
        f"- **Reproduced:** {result.reproduced}",
        f"- **Crash:** {result.crash_observed}",
        f"- **Verdict:** {result.verdict}",
    ]
    if result.errors:
        lines.append("")
        lines.append("## Errors")
        for error in result.errors:
            lines.append(f"- {error}")
    if result.findings:
        lines.append("")
        lines.append(result.findings)
    return "\n".join(lines)
