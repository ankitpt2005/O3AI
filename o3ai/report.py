"""Report generator for O3AI Working Tower evidence and human approval gate.

Reads results/*.json and compiles:
  1. results/report.md - Markdown summary for GitLab issue comments
  2. results/tower.html - Visual tower dashboard with computed metrics
All counts and statuses are computed dynamically, never hardcoded.
"""

from __future__ import annotations
import html
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from o3ai.evidence import (
    FLOOR_CHECKLISTS,
    REQUIRED_STAGES,
    derive_all_floor_statuses,
    load_evidence,
)
from o3ai.models import StageStatus


def compute_metrics(results_dir: Path) -> Dict[str, Any]:
    """Compute aggregate counts and percentages from results/*.json."""
    statuses = derive_all_floor_statuses(results_dir)
    total_floors = len(REQUIRED_STAGES)
    passed_count = sum(1 for s in statuses.values() if s == StageStatus.PASS)
    pending_count = sum(1 for s in statuses.values() if s == StageStatus.PENDING)
    needs_evidence_count = sum(1 for s in statuses.values() if s == StageStatus.NEEDS_EVIDENCE)
    failed_count = sum(1 for s in statuses.values() if s == StageStatus.FAIL)

    completion_pct = round((passed_count / total_floors) * 100) if total_floors > 0 else 0

    stage_records: Dict[str, Dict[str, Any]] = {}
    for stage in REQUIRED_STAGES:
        try:
            record = load_evidence(stage, results_dir)
        except Exception:
            record = {"status": statuses[stage].value, "summary": "No evidence recorded.", "evidence": {}}
        stage_records[stage] = record

    session_data: Dict[str, Any] = {}
    session_file = results_dir / "session.json"
    if session_file.exists():
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                session_data = json.load(f)
        except Exception:
            pass

    return {
        "total_floors": total_floors,
        "passed_count": passed_count,
        "pending_count": pending_count,
        "needs_evidence_count": needs_evidence_count,
        "failed_count": failed_count,
        "completion_pct": completion_pct,
        "all_passed": passed_count == total_floors,
        "statuses": statuses,
        "stage_records": stage_records,
        "session": session_data,
    }


def generate_markdown_report(metrics: Dict[str, Any]) -> str:
    """Format evidence summary into markdown with dynamically computed counts."""
    floor_names = {
        "planner": "Floor 1: Planner",
        "builder": "Floor 2: Builder",
        "security_qa": "Floor 3: Security & QA",
        "release_monitor": "Floor 4: Release & Monitor",
    }

    status_icons = {
        StageStatus.PASS: "🟢 PASS",
        StageStatus.NEEDS_EVIDENCE: "🟡 NEEDS_EVIDENCE",
        StageStatus.PENDING: "⚪ PENDING",
        StageStatus.FAIL: "🔴 FAIL",
    }

    session_id = metrics.get("session", {}).get("session_id", "N/A")
    lines: List[str] = [
        "# 🏛️ O3AI Working Tower: Evidence Report",
        "",
        f"**Session ID:** `{session_id}`  ",
        f"**Tower Progress:** {metrics['passed_count']}/{metrics['total_floors']} Floors Passed ({metrics['completion_pct']}%)  ",
        f"**Status Counts:** {metrics['passed_count']} Passed, {metrics['needs_evidence_count']} Needs Evidence, {metrics['pending_count']} Pending, {metrics['failed_count']} Failed",
        "",
        "| Floor | Stage | Status | Summary |",
        "|---|---|---|---|",
    ]

    for stage in REQUIRED_STAGES:
        status: StageStatus = metrics["statuses"][stage]
        record = metrics["stage_records"].get(stage, {})
        summary = record.get("summary", "No evidence recorded.")
        icon = status_icons.get(status, "⚪ PENDING")
        name = floor_names.get(stage, stage)
        lines.append(f"| {name} | `{stage}` | {icon} | {summary} |")

    lines.append("")
    if metrics["all_passed"]:
        lines.extend([
            "### 🚦 Human Approval Gate: READY FOR DEPLOYMENT",
            "> All 4 floors verified with deterministic evidence.",
            "",
            "To deploy, a human reviewer must sign off by commenting:",
            "```text",
            "/approve",
            "```",
        ])
    else:
        lines.extend([
            "### ⛔ Human Approval Gate: BLOCKED",
            f"> {metrics['total_floors'] - metrics['passed_count']} floor(s) pending verification.",
        ])

    return "\n".join(lines)


def generate_tower_html(metrics: Dict[str, Any]) -> str:
    """Generate self-contained visual dashboard for Working Tower with computed stats."""
    status_classes = {
        StageStatus.PASS: "badge-pass",
        StageStatus.NEEDS_EVIDENCE: "badge-warn",
        StageStatus.PENDING: "badge-pending",
        StageStatus.FAIL: "badge-fail",
    }

    floor_meta = {
        "release_monitor": {
            "title": "Floor 4: Release & Monitor",
            "agents": "ReleasePackagerAgent, DeployGateAgent",
            "desc": "Release manifest, rollback definition, human deployment sign-off.",
        },
        "security_qa": {
            "title": "Floor 3: Security & QA",
            "agents": "SecretScannerAgent, QARunnerAgent",
            "desc": "Secret leak detection, zero credential policy, test runner.",
        },
        "builder": {
            "title": "Floor 2: Builder",
            "agents": "CodeBuilderAgent, TestGeneratorAgent",
            "desc": "Standard library patch implementation, unit test assertions.",
        },
        "planner": {
            "title": "Floor 1: Planner",
            "agents": "PlannerAgent, DAGValidatorAgent",
            "desc": "Decompose goal into discrete, verifiable acyclic tasks.",
        },
    }

    # Render floor cards in tower order: Floor 4 at top down to Floor 1 at base
    cards_html = []
    tower_order = ["release_monitor", "security_qa", "builder", "planner"]
    for stage in tower_order:
        status: StageStatus = metrics["statuses"][stage]
        record = metrics["stage_records"].get(stage, {})
        meta = floor_meta[stage]
        cls = status_classes.get(status, "badge-pending")
        summary_esc = html.escape(str(record.get("summary", "Pending floor execution.")))
        evidence_json = html.escape(json.dumps(record.get("evidence", {}), indent=2))

        cards_html.append(f"""
        <div class="floor-card">
          <div class="floor-header">
            <div>
              <h2 class="floor-title">{meta['title']}</h2>
              <p class="floor-desc">{meta['desc']}</p>
            </div>
            <span class="badge {cls}">{status.value}</span>
          </div>
          <div class="agent-tag">Agents: {meta['agents']}</div>
          <p class="summary-text"><strong>Summary:</strong> {summary_esc}</p>
          <details class="evidence-box">
            <summary>View Deterministic Evidence ({stage}.json)</summary>
            <pre><code>{evidence_json}</code></pre>
          </details>
        </div>
        """)

    session_id = html.escape(str(metrics.get("session", {}).get("session_id", "active-goal-session")))
    pct = metrics["completion_pct"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>O3AI Working Tower Dashboard</title>
  <style>
    :root {{
      --bg: #0b0f19;
      --card-bg: #151e32;
      --border: #24324f;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --pass: #22c55e;
      --warn: #f59e0b;
      --fail: #ef4444;
      --pending: #64748b;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 2rem 1rem;
      min-height: 100vh;
    }}
    .container {{
      max-width: 900px;
      margin: 0 auto;
    }}
    header {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.5rem;
      margin-bottom: 2rem;
    }}
    h1 {{ font-size: 1.75rem; margin-bottom: 0.5rem; color: #fff; }}
    .meta-bar {{ color: var(--text-muted); font-size: 0.95rem; margin-bottom: 1rem; }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 1rem;
      margin: 1rem 0;
    }}
    .stat-box {{
      background: rgba(0,0,0,0.25);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.75rem;
      text-align: center;
    }}
    .stat-val {{ font-size: 1.5rem; font-weight: bold; color: var(--accent); }}
    .stat-lbl {{ font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; }}
    .progress-bar {{
      height: 10px;
      background: var(--border);
      border-radius: 5px;
      overflow: hidden;
      margin-top: 1rem;
    }}
    .progress-fill {{
      height: 100%;
      background: linear-gradient(90deg, #38bdf8, #22c55e);
      width: {pct}%;
      transition: width 0.3s ease;
    }}
    .tower {{
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }}
    .floor-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.25rem;
      transition: transform 0.2s, border-color 0.2s;
    }}
    .floor-card:hover {{
      transform: translateY(-2px);
      border-color: var(--accent);
    }}
    .floor-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 0.75rem;
    }}
    .floor-title {{ font-size: 1.2rem; color: #fff; }}
    .floor-desc {{ font-size: 0.85rem; color: var(--text-muted); margin-top: 2px; }}
    .badge {{
      display: inline-block;
      padding: 0.25rem 0.6rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 700;
      letter-spacing: 0.5px;
    }}
    .badge-pass {{ background: rgba(34, 197, 94, 0.2); color: var(--pass); border: 1px solid var(--pass); }}
    .badge-warn {{ background: rgba(245, 158, 11, 0.2); color: var(--warn); border: 1px solid var(--warn); }}
    .badge-fail {{ background: rgba(239, 68, 68, 0.2); color: var(--fail); border: 1px solid var(--fail); }}
    .badge-pending {{ background: rgba(100, 116, 139, 0.2); color: var(--pending); border: 1px solid var(--pending); }}
    .agent-tag {{ font-size: 0.8rem; color: var(--accent); margin-bottom: 0.5rem; }}
    .summary-text {{ font-size: 0.9rem; color: #cbd5e1; margin-bottom: 0.5rem; }}
    .evidence-box {{
      margin-top: 0.5rem;
      background: rgba(0,0,0,0.3);
      padding: 0.5rem;
      border-radius: 6px;
      font-size: 0.8rem;
    }}
    .evidence-box summary {{ cursor: pointer; color: var(--text-muted); outline: none; }}
    .evidence-box pre {{ margin-top: 0.5rem; overflow-x: auto; color: #38bdf8; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🏛️ O3AI Working Tower Dashboard</h1>
      <div class="meta-bar">Session: <code>{session_id}</code></div>
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-val">{metrics['passed_count']}/{metrics['total_floors']}</div>
          <div class="stat-lbl">Floors Passed</div>
        </div>
        <div class="stat-box">
          <div class="stat-val">{metrics['needs_evidence_count']}</div>
          <div class="stat-lbl">Needs Evidence</div>
        </div>
        <div class="stat-box">
          <div class="stat-val">{metrics['pending_count']}</div>
          <div class="stat-lbl">Pending</div>
        </div>
        <div class="stat-box">
          <div class="stat-val" style="color: var(--fail);">{metrics['failed_count']}</div>
          <div class="stat-lbl">Failed</div>
        </div>
        <div class="stat-box">
          <div class="stat-val">{metrics['completion_pct']}%</div>
          <div class="stat-lbl">Completion</div>
        </div>
      </div>
      <div class="progress-bar">
        <div class="progress-fill"></div>
      </div>
    </header>
    <main class="tower">
      {''.join(cards_html)}
    </main>
  </div>
</body>
</html>"""


def generate_reports(results_dir: Path) -> Tuple[Path, Path]:
    """Generate both tower.html and report.md in results directory."""
    results_dir.mkdir(parents=True, exist_ok=True)
    metrics = compute_metrics(results_dir)

    md_content = generate_markdown_report(metrics)
    report_md_file = results_dir / "report.md"
    with open(report_md_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    html_content = generate_tower_html(metrics)
    tower_html_file = results_dir / "tower.html"
    with open(tower_html_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    return tower_html_file, report_md_file
