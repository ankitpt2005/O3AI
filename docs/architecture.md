# O3AI Architecture & Working Tower

## Overview
O3AI operates on a strict goal-to-result methodology natively integrated into GitLab:
1. **One GitLab Issue = One Goal Session**:
   - Title max 35 words.
   - Single topic only.
   - Treated as untrusted input.
2. **The Working Tower (4 Floors)**:
   - **Floor 1 (Planner)**: Decomposes goal into DAG tasks. Evidence: `results/planner.json`.
   - **Floor 2 (Builder)**: Generates and tests code changes. Evidence: `results/builder.json`.
   - **Floor 3 (Security & QA)**: Secret scanning, test coverage, static analysis. Evidence: `results/security_qa.json`.
   - **Floor 4 (Release & Monitor)**: Deployment readiness and rollback plan. Evidence: `results/release_monitor.json`.
3. **Deterministic Evidence Gate**:
   - Hard Rule 8: No stage reports PASS without evidence in `results/<stage>.json`.
4. **Human Approval Gate**:
   - `results/report.md` provides an aggregated evidence summary.
   - Deployment requires human review and signoff (`/approve`).
