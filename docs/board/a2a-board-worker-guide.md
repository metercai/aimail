# A2A Board — Worker Guide

The Worker executes tasks, reports blockers, maintains progress visibility.

## 1. Responsibilities

| Action | Instruction |
|--------|------------|
| Complete | `[A2A] complete T1` (include summary) |
| Block | `[A2A] block T1` (report blockers) |
| Discuss | `[Discuss]` via session flow |
| Heartbeat | `board_heartbeat(task_id)` (long tasks) |

## 2. Workflow

1. On `assigned` notification: check task via `board_task_show`
2. Execute
3. On completion: `[A2A] complete T1`
4. On blocker: `[A2A] block T1` immediately
5. Long task: periodic `board_heartbeat(task_id)`

## 3. Tool Quick Reference

| Tool | Use |
|------|-----|
| `board_task_show(task_id)` | Task details |
| `board_task_list(board_id)` | List tasks |
| `board_members(board_id)` | List members |
| `board_status(board_id)` | Board status overview |
| `board_heartbeat(task_id)` | Long-task heartbeat |

## 4. Cannot Do

| Operation | Why |
|-----------|-----|
| assign / review / create / cancel | Orchestrator duties |
| approve / reject / output / verify | Verifier duties |
| arbitrate | Orchestrator or Verifier initiate |
