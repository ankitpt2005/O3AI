# O3AI Tower Plan: Add input validation to signup: reject invalid emails and passwords under 8 chars.

## 1. Problem Statement
- **What**: Add input validation to signup: reject invalid emails and passwords under 8 chars.
- **Why**: Protect account integrity and prevent malformed registrations.
- **When/Where**: sample_app/signup.py
- **Constraints**:
  - Python 3.12 standard library only
  - Zero external dependencies
  - Sub-millisecond response time
- **Done-when**:
  - Email regex validation rejects invalid format
  - Password min length 8 characters enforced

## 2. Evidence Questions (Max 5)
1. [EV-PLAN-1] (planner): Are all goals decomposed into discrete, verified DAG tasks?
2. [EV-BLD-1] (builder): Do all generated files parse cleanly and pass unit tests?
3. [EV-SEC-1] (security_qa): Are there zero leaked secrets and tests passed?
4. [EV-REL-1] (release_monitor): Is release artifact packaged with rollback steps documented?

## 3. Working Tower DAG Tasks
- **[task-1-spec]** Analyze Problem Statement: Validate scope: Add input validation to signup: reject invalid emails and passwords under 8 char
- **[task-2-build]** Implement Code Patch: Apply standard library solution in sample_app/signup.py (depends on task-1-spec)
- **[task-3-verify]** Execute Unit Tests & QA: Run test suite and verify 0 failures and 0 secrets. (depends on task-2-build)
- **[task-4-release]** Compile Release Evidence: Generate evidence report and stage deployment manifest. (depends on task-3-verify)