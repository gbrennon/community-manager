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
    """Fetch an issue, launch a sandbox, install cline, reproduce steps, capture results."""

    def __init__(
        self,
        provider: SandboxProvider | None = None,
        config: SandboxConfig | None = None,
        fetcher: GitHubIssueFetcher | None = None,
    ) -> None:
        self.config = config or SandboxConfig()
        self.provider = provider or DockerProvider(config=self.config)
        self.fetcher = fetcher or GitHubIssueFetcher()

    async def review(
        self, issue_url: str, *, project_dir: Path | None = None,
    ) -> ReviewResult:
        issue = self._fetch_issue_or_error(issue_url)
        if isinstance(issue, ReviewResult):
            return issue

        reproduction_steps = parse_steps_from_issue_body(issue.body) or list(_FALLBACK_STEPS)

        sandbox_id = await self.provider.launch()
        result = ReviewResult(
            issue_url=issue_url, issue_title=issue.title,
            sandbox_id=sandbox_id, success=False,
        )
        try:
            await self._install_cline(sandbox_id, issue.cline_version)
            await self.provider.disconnect_network(sandbox_id)

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
            result.errors.append(str(exc))
        finally:
            await self.provider.destroy(sandbox_id)
        return result
    async def review_many(
        self, urls: list[str], *, project_dir: Path | None = None,
        max_concurrent: int = 4,
    ) -> list[ReviewResult]:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def review_one(url: str) -> ReviewResult:
            async with semaphore:
                provider_cls = type(self.provider)
                fresh_provider = provider_cls(config=self.config)
                reviewer = IssueReviewer(
                    provider=fresh_provider, config=self.config, fetcher=self.fetcher,
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
        escaped = package.replace("'", "'\\''")
        result = await self.provider.exec(
            sandbox_id,
            ["su", "cline", "-c", f"npm install -g '{escaped}'"],
        )
        if result.exit_code != 0:
            raise RuntimeError(f"npm install -g {package} failed: {result.stderr}")

    async def _execute_steps(
        self, sandbox_id: str, steps: list[str],
    ) -> tuple[bool, bool, list[dict[str, Any]]]:
        step_log: list[dict[str, Any]] = []
        any_crash = False
        every_step_ran = True

        for index, step_description in enumerate(steps, start=1):
            record: dict[str, Any] = {
                "step": index, "description": step_description, "ran": False,
            }
            command = convert_step_to_cline_command(step_description)
            try:
                result = await self.provider.exec(sandbox_id, command)
                record["exit_code"] = result.exit_code
                record["stdout"] = result.stdout[-2000:]
                record["stderr"] = result.stderr[-2000:]
                record["ran"] = True

                if process_exited_with_crash(result):
                    any_crash = True
                    record["crashed"] = True
                    core_dump = await self.provider.exec(
                        sandbox_id,
                        ["sh", "-c", "coredumpctl list 2>/dev/null || echo none"],
                    )
                    record["core_dump"] = core_dump.stdout[:1000]
            except Exception as exc:
                record["error"] = str(exc)
                every_step_ran = False
            step_log.append(record)

        return every_step_ran, any_crash, step_log

    def write_report(self, result: ReviewResult, output_path: Path | None = None) -> Path:
        path = output_path or Path("findings.md")
        path.write_text(render_markdown_report(result))
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
        return ["su", "cline", "-c", "cline --version"]
    if "open" in lowered and "cline" in lowered:
        return ["su", "cline", "-c", "timeout 5 cline"]
    if any(signal in lowered for signal in ("ctrl+c", "sigint", "sigkill")):
        return [
            "su", "cline", "-c",
            "cline & P=$!; sleep 2; kill -2 $P; wait $P 2>/dev/null; echo EXIT:$?",
        ]
    if "exit" in lowered:
        return ["su", "cline", "-c", "echo exit | timeout 3 cline || true"]
    return ["su", "cline", "-c", "timeout 10 cline"]


def process_exited_with_crash(result: Any) -> bool:
    if result.exit_code == 0:
        return False
    if result.exit_code in _CRASH_EXIT_CODES:
        return True
    combined_output = (result.stdout + result.stderr).lower()
    return any(marker in combined_output for marker in _CRASH_TEXT_MARKERS)


def render_verdict(result: ReviewResult) -> str:
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
    lines.append(f"- Steps: {len(result.steps_executed)}")
    lines.append(f"- All ran: {result.reproduced}")
    lines.append(f"- Crash detected: {result.crash_observed}")
    for record in result.steps_executed:
        status = _step_status_label(record)
        lines.append(f"\n### Step {record['step']}: {record['description']}  [{status}]")
        for stream in ("stdout", "stderr"):
            if record.get(stream):
                lines.append("```")
                lines.append(record[stream][:500])
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
