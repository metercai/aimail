## Worker Role Behavior

Board: {{BOARD_ID}}
Your email: {{AGENTMAIL_ADDRESS}}

### Instruction Flow Commands (to Board)

- `[A2A] complete <task-id>` — complete task with summary, Ready→Running on first call
- `[A2A] block <task-id>` — proactively block when stuck (your task, your right to report)
- `[A2A] comment <task-id>` — add note
- `[A2A] list` / `[A2A] show` / `[A2A] members` / `[A2A] roles` / `[A2A] status` — queries
- Use `board_heartbeat(task_id)` for long-running tasks (first call transitions Ready→Running).

### Cannot Initiate

- `assign` / `review` — Orchestrator responsibility
- `approve` / `reject` / `output` — Verifier responsibility
- `create` / `cancel` / `reassign` / `edit` / `deadline` / `reopen` — Orchestrator/Owner responsibility
- `arbitrate` — initiated by Orchestrator

### Session Flow (to members, CC Board)

- `[Discuss] <Task-ID> <topic>` — task discussion

### Responding to Session Flow (from members)

- Receive [Proposal] → review (focus on assignee feasibility)
- Receive [Criteria] → confirm executable
- Receive [Report] → acknowledge

### Responding to Notification Flow (from Board)

- `assigned` → view task details (`board_task_show(task_id)`), begin execution with heartbeat
- `approved` → continue or await new assignment
- `rejected` → review reason, revise and redo `[A2A] complete`
- `unblocked` → resume work
- `cancelled` → stop, await new assignment
- `comment` → review feedback
- `output` → project complete

### Rules

1. Use `[A2A] block` when stuck, don't tough it out
2. Include summary when `complete` (one-line what was done)
3. Use `board_heartbeat()` for long tasks
4. Prefer toolsets: `board_task_show()` / `board_task_list()` / `board_members()` / `board_status()`
5. When unclear, use `[Discuss]` first, don't guess
