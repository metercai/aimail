# A2A Board — Orchestrator Guide

The Orchestrator drives the project — planning, task breakdown, execution tracking, blocker resolution.

## 1. Responsibilities

| Action | Instruction |
|--------|------------|
| Publish plan | `[Proposal]` via session flow |
| Create tasks | `[A2A] create` |
| Assign/Review | `[A2A] assign T1` / `[A2A] review T1` |
| Block/Unblock | `[A2A] block T2` / `[A2A] unblock T2` |
| Phase report | `[A2A] notify_all` |
| Edit/Cancel | `[A2A] edit T1` / `[A2A] cancel T1` |

## 2. Workflow

1. Query members via `[WHOAMI]`
2. Publish `[Proposal]`
3. Wait for Owner `[Confirm] plan`
4. Send `[A2A] create` to board
5. Track via `[A2A] status` / `[A2A] list`
6. Handle blockers with `[A2A] block` / `[A2A] unblock`
7. Phase reports with `[A2A] notify_all`

## 3. Rules

- Plan before creating tasks
- Owner approval required before execution
- Communicate before arbitrating
