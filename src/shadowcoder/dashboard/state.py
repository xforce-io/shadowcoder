"""Central state container — reads issue files and produces dashboard data."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from shadowcoder.dashboard.parsers import (
    FeedbackParser,
    LogParser,
    WorktreeParser,
)

_ISSUE_GLOB = "[0-9][0-9][0-9][0-9]"

_STATUS_PROGRESS = {
    "created": 0, "designing": 0, "design_review": 1,
    "approved": 2, "developing": 2, "dev_review": 4, "done": 6,
    "blocked": -1, "failed": -1, "cancelled": -1,
}

_PIPELINE_STAGES = ["Design", "Design Review", "Develop", "Gate", "Dev Review", "Acceptance"]

_GATE_FAIL_RE = re.compile(r"Gate FAIL R(\d+)")
_GATE_PASS_RE = re.compile(r"Gate PASS R(\d+)")
_METRIC_FAIL_RE = re.compile(r"Metric gate FAIL|metric_gate", re.IGNORECASE)
_COST_RE = re.compile(r"\$(\d+\.?\d*)")


@dataclass
class PipelineStage:
    name: str
    state: str  # "completed", "active", "pending", "failed", "reverted"


class DashboardState:
    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path
        self.issues_dir = Path(repo_path) / ".shadowcoder" / "issues"
        self.worktrees_dir = Path(repo_path) / ".shadowcoder" / "worktrees"

    def get_issue_list(self) -> list[dict]:
        issues: list[dict] = []
        if not self.issues_dir.exists():
            return issues
        for d in sorted(self.issues_dir.glob(_ISSUE_GLOB)):
            if not d.is_dir():
                continue
            md_path = d / "issue.md"
            if not md_path.exists():
                continue
            try:
                post = frontmatter.load(str(md_path))
                issues.append({
                    "id": post["id"], "title": post["title"],
                    "status": post["status"], "updated": post.get("updated", ""),
                })
            except Exception:
                continue
        return issues

    def get_issue_detail(self, issue_id: int) -> dict | None:
        d = self.issues_dir / f"{issue_id:04d}"
        md_path = d / "issue.md"
        if not md_path.exists():
            return None
        try:
            post = frontmatter.load(str(md_path))
        except Exception:
            return None

        status = post["status"]
        log_path = d / "issue.log"
        log_raw = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        log_entries = LogParser.parse_all(log_raw)

        fb_path = d / "feedback.json"
        if fb_path.exists():
            fb_data = json.loads(fb_path.read_text(encoding="utf-8"))
        else:
            fb_data = {"items": [], "proposed_tests": [],
                       "acceptance_tests": [], "supplementary_tests": []}
        feedback_summary = FeedbackParser.summarize(fb_data)
        feedback = {
            "verdict": feedback_summary.verdict,
            "critical": feedback_summary.critical,
            "high": feedback_summary.high,
            "medium": feedback_summary.medium,
            "low": feedback_summary.low,
            "total": feedback_summary.total,
            "passed": feedback_summary.passed,
        }

        pipeline = self._build_pipeline(status, log_entries)
        retries = self._extract_retries(log_entries)

        wt_path = self.worktrees_dir / f"issue-{issue_id}"
        changed_files = WorktreeParser.get_changed_files(str(wt_path)) if wt_path.exists() else []

        cost = self._extract_cost(log_raw)
        gate_output = self._extract_gate_output(log_entries)

        return {
            "id": post["id"], "title": post["title"], "status": status,
            "assignee": post.get("assignee"), "created": post.get("created", ""),
            "updated": post.get("updated", ""), "blocked_reason": post.get("blocked_reason"),
            "log_entries": log_entries, "feedback": feedback, "pipeline": pipeline,
            "retries": retries, "changed_files": changed_files, "cost": cost,
            "gate_output": gate_output,
        }

    def _build_pipeline(self, status: str, log_entries) -> list[PipelineStage]:
        progress = _STATUS_PROGRESS.get(status, 0)
        stages: list[PipelineStage] = []
        for i, name in enumerate(_PIPELINE_STAGES):
            if progress == -1:
                stage_state = self._infer_stage_from_log(i, status, log_entries)
            elif i < progress:
                stage_state = "completed"
            elif i == progress:
                stage_state = "active"
            else:
                stage_state = "pending"
            stages.append(PipelineStage(name=name, state=stage_state))
        return stages

    def _infer_stage_from_log(self, stage_idx: int, status: str, log_entries) -> str:
        log_text = " ".join(e.text for e in log_entries)
        completed_markers = [
            re.search(r"Design R\d", log_text) is not None,
            re.search(r"Design Review", log_text) is not None,
            re.search(r"Develop R\d", log_text) is not None,
            re.search(r"Gate (PASS|FAIL)", log_text) is not None,
            re.search(r"Dev Review|develop_review", log_text, re.IGNORECASE) is not None,
            re.search(r"Acceptance|acceptance", log_text, re.IGNORECASE) is not None,
        ]
        if stage_idx < len(completed_markers) and completed_markers[stage_idx]:
            later_started = any(completed_markers[stage_idx + 1:])
            if later_started:
                return "completed"
            if status in ("blocked", "failed"):
                return "failed"
            return "completed"
        return "pending"

    def _extract_retries(self, log_entries) -> list[dict]:
        retries: list[dict] = []
        for entry in log_entries:
            m = _GATE_FAIL_RE.search(entry.text)
            if m:
                summary = entry.text.split(":", 1)[1].strip() if ":" in entry.text else ""
                retries.append({"round": f"R{m.group(1)}", "result": "fail",
                                "type": "test", "summary": summary})
                continue
            m = _GATE_PASS_RE.search(entry.text)
            if m:
                retries.append({"round": f"R{m.group(1)}", "result": "pass",
                                "type": "test", "summary": ""})
                continue
            if _METRIC_FAIL_RE.search(entry.text):
                retries.append({"round": "Attempt", "result": "reverted",
                                "type": "metric", "summary": entry.text})
        return retries

    def _extract_cost(self, log_raw: str) -> str:
        for line in reversed(log_raw.splitlines()):
            if "Cost:" in line or "cost" in line.lower():
                m = _COST_RE.search(line)
                if m:
                    return f"${m.group(1)}"
        total = 0.0
        for line in log_raw.splitlines():
            if line.strip().startswith("[") and "Usage:" in line:
                m = _COST_RE.search(line)
                if m:
                    total += float(m.group(1))
        if total > 0:
            return f"${total:.2f}"
        return "$0.00"

    def _extract_gate_output(self, log_entries) -> str | None:
        for entry in reversed(log_entries):
            if "Gate" in entry.text:
                lines = [entry.text] + entry.continuation
                return "\n".join(lines)
        return None
