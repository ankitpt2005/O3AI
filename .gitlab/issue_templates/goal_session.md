---
name: Goal Session
about: Standard template for initiating an O3AI Goal Session
title: "[Goal] "
labels: ["o3ai::goal-session"]
---

### Goal
<!-- What is the single outcome to achieve? Keep issue title under 35 words. -->

### Why
<!-- Business or technical justification for this goal. -->

### Where/When
<!-- Target repository path, environment, or component affected. -->

### Constraints
- Python 3.12 standard library only
- No external secrets, API keys, or unauthorized dependencies
- Deterministic verification required in results/<stage>.json

### Done-when
<!-- Verifiable completion criteria (e.g., all tests pass, zero secret alerts, human approval granted). -->
- [ ] Working Tower Floor 1 (Planner): Verified DAG task breakdown
- [ ] Working Tower Floor 2 (Builder): Working implementation + unit tests
- [ ] Working Tower Floor 3 (Security & QA): Secret scanning and test suite clean
- [ ] Working Tower Floor 4 (Release & Monitor): Evidence report ready
- [ ] Human Approval: Signed off before deployment
