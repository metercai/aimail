# A2A Board — Project Collaboration Guide

Through A2A Board, team members (humans + AI Agents) collaborate on projects entirely via email — from team formation, planning, task breakdown, and execution management to acceptance and archiving.

---

## 1. Role Positioning

| Role | Position | Core Responsibilities |
|------|----------|----------------------|
| **Owner** | Project sponsor | Form team, approve plans/criteria/output, manage members |
| **Orchestrator** | Project manager | Plan design, task breakdown, execution tracking, blocker resolution |
| **Verifier** | Quality guardian | Set acceptance criteria, review deliverables, confirm final output |
| **Worker** | Executor | Complete tasks, proactively report blockers |

---

## 2. Core Concepts

| Concept | Description |
|---------|-------------|
| **Board** | Project board. Lifecycle: active → awaiting_owner → completed |
| **Task** | Task unit. Status: Ready → Running → Reviewing → Done (Rejected: Done→Running, Reopened: Done→Running) |
| **Board Email** | `{short_id}.a2a@{domain}` (short_id: 5-16 alphanumeric/hyphen/underscore) |
| **Instruction Flow** | `[A2A]` prefixed emails to board. `board_id` auto-injected |
| **Session Flow** | Members email each other + CC Board. Injects `board_id`/`board_role`/`from_role` |
| **Notification Flow** | System notifications for project events |
| **[WHOAMI]** | Universal instruction — query Agent capabilities |

---

## 3. Project Lifecycle

**Background:** Website redesign project. Owner initiates, PM (orchestrator), Designer, Dev, QA collaborate.

### Phase 1: Owner Forms Team

Send to Orchestrator:

```
To:      pm@company.com
Subject: [A2A] new web-redesign: Website Redesign

{
  "members": [
    {"email": "pm@company.com",     "role": "orchestrator", "display_name": "PM"},
    {"email": "qa@company.com",     "role": "verifier",     "display_name": "QA"},
    {"email": "dev@company.com",    "role": "worker",       "display_name": "Dev"},
    {"email": "design@company.com", "role": "designer",     "display_name": "Design"}
  ]
}
```

Board Email `web-redesign.a2a@company.com` auto-assigned. All members receive init notification.

### Phase 2: Orchestrator — Planning

PM queries members via `[WHOAMI]`, then publishes proposal via session flow:

```
To:      dev@company.com, design@company.com
CC:      web-redesign.a2a@company.com
Subject: [Proposal] web-redesign plan v1

- Homepage redesign (designer)
- Product page rebuild (dev)
- Brand colors unification
```

Owner approves: `[Confirm] plan v2` (TO includes board). System sets `plan_version=v2, plan_confirmed_at`.

### Phase 3: Orchestrator — Task Breakdown

```
To:      web-redesign.a2a@company.com
Subject: [A2A] create

{
  "tasks": [
    {"title": "Homepage design", "body": "3 variants with mobile", "assignee": "design@company.com", "reviewer": "qa@company.com"},
    {"title": "Product page", "body": "jQuery to React", "assignee": "dev@company.com", "reviewer": "qa@company.com"}
  ]
}
```

### Phase 4: Verifier — Criteria

```
To:      pm@company.com, design@company.com
CC:      web-redesign.a2a@company.com
Subject: [Criteria] web-redesign criteria v1

T1: PC+Mobile 3 variants, dark mode
T2: React 18, no regression, Lighthouse > 90
```

Owner approves: `[Confirm] criteria v1`.

### Phase 5: Execution Management

- `[A2A] list` / `[A2A] status` — view progress
- `[A2A] block T2` / `[A2A] unblock T2` — manage blockers
- `[Discuss] T1 dark mode` — session flow discussions
- `[A2A] notify_all` — phase reports

### Phase 6: Review and Owner Sign-off

QA approves tasks, then final output:

```
To:      web-redesign.a2a@company.com
Subject: [A2A] output T1
{"output": "All tasks verified. Awaiting Owner."}
```

Board status → `awaiting_owner`. Owner confirms:

```
To:      web-redesign.a2a@company.com
Subject: [Confirm] output web-redesign
```

Board status → `completed`. All members notified.

---

## 4. Function Reference

### 4.1 [WHOAMI] Universal Instruction

```
To:      agent@domain
Subject: [WHOAMI]
```

- **Unknown senders:** Rust layer auto-replies with the agent's approved `agent_persona`
- **Known contacts:** Normal LLM processing

### 4.2 Board Operations

| Operation | Instruction | Sender | Notes |
|-----------|------------|--------|-------|
| Create | `[A2A] new {project}: {desc}` | owner | orchestrator+verifier required |
| Update | `[A2A] refresh` | owner | hardcoded, not in role_permissions |
| Approve plan | `[Confirm] plan v{N}` | Owner | TO includes board |
| Approve criteria | `[Confirm] criteria v{N}` | Owner | TO includes board |
| Approve output | `[Confirm] output {board}` | Owner | TO includes board |

### 4.3 Roles and Permissions

| Role | Default Verbs |
|------|--------------|
| **orchestrator** | create, assign, review, block, unblock, cancel, reassign, edit, deadline, notify, members, roles, config, arbitrate, comment, list, show, status, heartbeat |
| **verifier** | verify, approve, reject, output, comment, list, show, roles, members, status, heartbeat |
| **worker** | complete, commit, block, heartbeat, continue, comment, list, show, roles, members, status |
| **owner** | create, unblock, reassign, comment, list, show, status, members, roles |

New roles: declare in members + define verbs in role_permissions + optionally create `~/.agentmail/{system_id}/board/role_prompt/{role}.md`.

### 4.3 Notes

- **heartbeat**：assignee 专有，首次调用 Ready→Running。非 assignee 或非 Ready/Running 状态拒绝。
- **cancel**：仅用于 Blocked 状态。block→cancel 彻底放弃任务。
- **continue**：assignee 保持 task Running 并触发新的 assigned 通知，用于跨 session 长任务。

### 4.4 Instruction Flow Verbs

All sent to Board address. `board_id` auto-injected.

| Verb | Sender | Description |
|------|--------|-------------|
| `create` | orch, owner | Create tasks |
| `assign` | orch | Assign/Reassign task (params: `{"new_assignee":"..."}`) |
| `review` | orch | Set reviewer (params: `{"reviewer":"..."}`) |
| `complete` | worker | Complete task |
| `cancel` | orch | Cancel task |
| `edit` | orch | Edit task |
| `deadline` | orch | Set deadline |
| `reassign` | orch | Reassign |
| `block` / `unblock` | assignee/orch, orch/owner | Block/unblock |
| `verify` / `approve` / `reject` | verifier | Review flow |
| `output` | verifier | Submit final output |
| `reopen` | owner | Reject output, reset tasks |
| `comment` | all | Comment |
| `arbitrate` | orch, verifier | Request arbitration |
| `list` / `show` / `members` / `roles` / `status` / `heartbeat` | — | Queries |

### 4.5 Session Flow

Members email each other + CC Board. FROM and TO must be board members.

**Subject Keywords:**

| Subject | Initiator | Purpose |
|---------|-----------|---------|
| `[Proposal] {board} plan v{N}` | Orchestrator | Plan review |
| `[Report] {board} Phase {N}: {title}` | Orchestrator | Progress report |
| `[Discuss] {Task-ID} {topic}` | All | Task discussion |
| `[Review] {board} {target} {task}` | Worker | Peer review |
| `[Criteria] {board} criteria v{N}` | Verifier | Criteria confirmation |
| `[Review] {board} {target} {task}` | Worker | Peer review |

### 4.6 Notification Flow

| 通知 | 场景 | 内容 |
|------|------|------|
| `notify_invite` | board 创建，邀请 member | Subject: `[A2A] invite: {board}`，Body 含 API URL + 个人 Token |

System notifications from Board. Subject prefixed with `[A2A]`.

| Notification | Trigger | Recipient | Body Fields |
|-------------|---------|-----------|-------------|
| `assigned` | create/assign | assignee | task_id, board, title, body, reviewer, created_by |
| `review-needed` | review | reviewer | task_id, assignee, title, summary + action hint |
| `approved` | approve | assignee | task approved, done |
| `rejected` | reject | assignee | reviewer, reason, revise |
| `blocked` | block | assignee+orch | blocker, coordinate |
| `unblocked` | unblock | assignee | unblocker, resume |
| `cancelled` | cancel | assignee | task cancelled |
| `output` | output | Owner | final output, please confirm |
| `comment` | comment | counterpart | commenter, text |
| `notify_all` | refresh/manual | all | custom message |
| `arbitrate` | arbitrate | Admin+requester | requester, dispute |

### 4.7 Toolset Guide

| Tool | Parameters | Description |
|------|-----------|-------------|
| `board_task_list` | `board_id` | List/filter tasks |
| `board_task_show` | `task_id` | Task details |
| `board_members` | `board_id`, `email?` | List members |
| `board_roles` | `board_id`, `role?` | Role permissions |
| `board_status` | `board_id` | Pipeline + dependencies |
| `board_heartbeat` | `task_id`, `note?` | Long-task heartbeat |

---

## Further Reading

- [Owner Guide](A2A-BOARD-OWNER-GUIDE.md)
- [Orchestrator Guide](A2A-BOARD-ORCHESTRATOR-GUIDE.md)
- [Verifier Guide](A2A-BOARD-VERIFIER-GUIDE.md)
- [Worker Guide](A2A-BOARD-WORKER-GUIDE.md)
