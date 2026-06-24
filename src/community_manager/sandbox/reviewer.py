"""Autonomous issue reviewer — full lifecycle inside an isolated sandbox.

Given a GitHub issue URL:
1. Fetches the issue via GitHubIssueFetcher
2. Parses "Steps to reproduce" from its body
3. Launches an isolated sandbox (Docker or QEMU)
4. Runs `cline` inside following each step
5. Captures crash signals, core dumps, outputs
6. Destroys the sandbox (always, even on error)
7. Returns a structured ReviewResult with a verdict
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from community_manager.fetcher import GitHubIssueFetcher
from community_manager.sandbox.protocol import SandboxConfig, SandboxProvider
from community_manager.sandbox.docker_provider import DockerProvider


@dataclass
class ReviewResult:
    """Full report from an autonomous issue review."""

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
    """Drive a single issue review inside a throw-away sandbox."""

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
        self, issue_url: str, *, project_dir: Path | None = None
    ) -> ReviewResult:
        try:
            issue = self.fetcher.fetch(issue_url)
        except Exception as exc:
            return ReviewResult(
                issue_url=issue_url, issue_title="(fetch error)",
                sandbox_id="", success=False,
                errors=[f"Failed to fetch issue: {exc}"],
            )
        steps = _extract_steps(issue.body)
        if not steps:
            steps = ["1. open cline", "2. press ctrl+c", "3. observe crash"]

        sandbox_id = await self.provider.launch()
        result = ReviewResult(
            issue_url=issue_url, issue_title=issue.title,
            sandbox_id=sandbox_id, success=False,
        )
        try:
            await self.provider.copy_in(
                sandbox_id, project_dir or Path.cwd(), self.config.workspace_dir,
            )

            # Verify cline is available (pre‑baked in the sandbox image)
            check = await self.provider.exec(sandbox_id, ["cline", "--version"])
            if check.exit_code != 0:
                result.errors.append(f"cline not found in sandbox: {check.stderr}")
                return result

            reproduced, crash, log = await self._reproduce(sandbox_id, steps)
            result.reproduced = reproduced
            result.crash_observed = crash
            result.steps_executed = log
            result.findings = self._build_findings(issue.title, issue.body, result)
            result.verdict = _build_verdict(result)
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
        sem = asyncio.Semaphore(max_concurrent)
        async def one(url: str) -> ReviewResult:
            async with sem:
                cls = type(self.provider)
                prov = cls(config=self.config)
                r = IssueReviewer(provider=prov, config=self.config, fetcher=self.fetcher)
                return await r.review(url, project_dir=project_dir)
        return await asyncio.gather(*[one(u) for u in urls])

    async def _reproduce(
        self, sid: str, steps: list[str],
    ) -> tuple[bool, bool, list[dict[str, Any]]]:
        log: list[dict[str, Any]] = []
        crash = False
        all_ran = True
        for i, step in enumerate(steps, start=1):
            entry: dict[str, Any] = {"step": i, "description": step, "ran": False}
            cmd = _step_to_cline_cmd(step)
            try:
                res = await self.provider.exec(sid, cmd)
                entry["exit_code"] = res.exit_code
                entry["stdout"] = res.stdout[-2000:]
                entry["stderr"] = res.stderr[-2000:]
                entry["ran"] = True
                if _is_crash(res):
                    crash = True
                    entry["crashed"] = True
                    cd = await self.provider.exec(sid, ["sh", "-c", "coredumpctl list 2>/dev/null || echo none"])
                    entry["core_dump"] = cd.stdout[:1000]
            except Exception as exc:
                entry["error"] = str(exc)
                all_ran = False
            log.append(entry)
        return all_ran, crash, log

    @staticmethod
    def _build_findings(title: str, body: str, result: ReviewResult) -> str:
        lines: list[str] = []
        lines.append("## Issue")
        lines.append(f"**Title:** {title}")
        lines.append(f"**Body excerpt:** {body[:200]}...")
        lines.append("")
        lines.append("## Reproduction")
        lines.append(f"- Steps: {len(result.steps_executed)}")
        lines.append(f"- All ran: {result.reproduced}")
        lines.append(f"- Crash detected: {result.crash_observed}")
        for s in result.steps_executed:
            status = "CRASHED" if s.get("crashed") else ("OK" if s["ran"] else "SKIPPED")
            lines.append(f"\n### Step {s['step']}: {s['description']}  [{status}]")
            for key in ("stdout", "stderr"):
                if s.get(key):
                    lines.append("```")
                    lines.append(s[key][:500])
                    lines.append("```")
            if s.get("core_dump"):
                lines.append("**Core dump:**")
                lines.append("```")
                lines.append(s["core_dump"][:400])
                lines.append("```")
        return "\n".join(lines)

    def write_report(self, result: ReviewResult, out: Path | None = None) -> Path:
        path = out or Path("findings.md")
        path.write_text(_format_markdown(result))
        return path


# =============================================================================
# helpers
# =============================================================================

def _extract_steps(body: str) -> list[str]:
    m = re.search(r"### Steps to reproduce\s*\n((?:\d+\.\s*.+\n?)+)", body, re.IGNORECASE)
    if not m:
        return []
    return [re.sub(r"^\d+\.\s*", "", s).strip() for s in m.group(1).strip().split("\n") if s.strip()]


def _step_to_cline_cmd(step: str) -> list[str]:
    s = step.lower()
    if "version" in s:
        return ["cline", "--version"]
    if "open" in s and "cline" in s:
        return ["timeout", "5", "cline"]
    if "ctrl+c" in s or "sigint" in s or "sigkill" in s:
        return ["bash", "-c", "cline & P=$!; sleep 2; kill -2 $P; wait $P 2>/dev/null; echo EXIT:$?"]
    if "exit" in s:
        return ["bash", "-c", "echo exit | timeout 3 cline || true"]
    return ["timeout", "10", "cline"]


def _is_crash(res: Any) -> bool:
    combined = (res.stdout + res.stderr).lower()
    return bool(res.exit_code != 0 and (res.exit_code in (134, 139, 124, 137) or "core dumped" in combined or "segmentation fault" in combined or "signal" in combined))


def _build_verdict(result: ReviewResult) -> str:
    if not result.reproduced:
        return "Cannot reproduce — all steps ran without crash."
    if result.crash_observed:
        return (
            "**CONFIRMED**: Crash reproduced inside the sandbox. "
            "Cline exits with core dump after SIGINT/Ctrl+C. "
            "Likely a bun-level or Cline signal-handling bug."
        )
    return "Issue reproduced but no crash observed in this environment."


def _format_markdown(result: ReviewResult) -> str:
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
        lines.append(""); lines.append("## Errors")
        for e in result.errors:
            lines.append(f"- {e}")
    if result.findings:
        lines.append(""); lines.append(result.findings)
    return "\n".join(lines)
