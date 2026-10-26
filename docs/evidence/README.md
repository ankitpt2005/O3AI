# O3AI Evidence Archive & Recording Instructions

This directory archives verification evidence, execution logs, and visual screenshots for GitLab hackathon submission.

## Recording Instructions

### 1. Stage Evidence Files
Every pipeline run produces deterministic JSON files in `results/`:
- `results/validate.json`: Lint and syntax validation
- `results/test.json`: Unit test execution results
- `results/security.json`: Secret detection and static analysis proof
- `results/tower_report.json`: Tower dashboard generation metrics
- `results/approve.json`: Human review sign-off record
- `results/deploy.json`: GitLab Pages packaging record
- `results/monitor.json`: Health monitor HTTP verification response

### 2. Capturing Logs & Transcripts
To capture execution logs:
```powershell
python scripts/run_pipeline.py > docs/evidence/pipeline_run.log 2>&1
```

### 3. Capturing Visual Screenshots
Screenshots of the following screens should be saved in `docs/evidence/`:
1. `dashboard_green.png`: The deployed Working Tower dashboard (`public/index.html` or GitLab Pages) showing 100% completion.
2. `pipeline_stages.png`: GitLab CI/CD pipeline view showing all 7 stages green.
3. `human_approval_gate.png`: Issue comment or GitLab UI showing `/approve` manual sign-off.
