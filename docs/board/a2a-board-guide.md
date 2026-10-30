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
| **Board** | Project board. Lifecycle: active → awaiting_owner → completed; completed boards are archived by the system sweep |
| **Task** | Task unit. Main flow: Ready → Running → Reviewing → Done (reject applies only while Reviewing, back to Running; reopen is board-level — only while the board is awaiting_owner, all Done tasks reset to Running). Unassigned tasks sit in triage; children of pending parents sit in todo |
| **Board Email** | `{short_id}.a2a@{domain}` (short_id: 5-16 alphanumeric/hyphen/underscore). Shared-domain systems: `{short_id}.{system_name}.a2a@{shared_domain}` |
| **Instruction Flow** | `[A2A]` prefixed emails to board. `board_id` auto-injected |
| **Session Flow** | Members email each other + CC Board. Injects `board_id`/`board_role`/`from_role` |
| **Notification Flow** | System notifications for project events |
| **[WHOAMI]** | Universal instruction — query Agent capabilities |

---

## 3. Project Lifecycle

**Background:** Website redesign project. Owner initiates, PM (orchestrator), Dev, QA collaborate.

### Phase 1: Owner Forms Team

Send to Orchestrator (TO = orchestrator; the verifier must appear in TO or CC; the sender must be declared as an owner member):

```
To:      pm@company.com
CC:      qa@company.com
Subject: [A2A] new web-redesign: Website Redesign

{
  "members": [
    {"email": "owner@company.com",  "role": "owner",        "display_name": "Owner"},
    {"email": "pm@company.com",     "role": "orchestrator", "display_name": "PM"},
    {"email": "qa@company.com",     "role": "verifier",     "display_name": "QA"},
    {"email": "dev@company.com",    "role": "worker",       "display_name": "Dev"},
    {"email": "design@company.com", "role": "worker",       "display_name": "Design"}
  ]
}
```

Board Email `web-redesign.a2a@company.com` auto-assigned. All members receive the init notification (`[A2A] notice: Board web-redesign created — Website Redesign`).

### Phase 2: Orchestrator — Planning

PM queries members via `[WHOAMI]`, then publishes proposal via session flow:

```
To:      dev@company.com, design@company.com
CC:      web-redesign.a2a@company.com
Subject: [Proposal] web-redesign plan v1

- Homepage redesign (design)
- Product page rebuild (dev)
- Brand colors unification
```

Owner approves: `[Confirm] plan v1` (TO includes board). System sets `plan_version=v1, plan_confirmed_at`.

### Phase 3: Orchestrator — Task Breakdown

```
To:      web-redesign.a2a@company.com
Subject: [A2A] create

{
  "tasks": [
    {"title": "Homepage design", "body": "3 variants with mobile", "assignee": "design@company.com", "reviewer": "qa@company.com"},
    {"title": "Product page", "body": "jQuery to React", "assignee": "dev@company.com", "reviewer": "qa@company.com"},
    {"title": "Brand color unification", "body": "Replace global CSS variables", "assignee": "design@company.com", "reviewer": "qa@company.com"}
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
T3: Brand color unification — global CSS variables replaced, no hardcoded colors left
```

Owner approves: `[Confirm] criteria v1`.

### Phase 5: Execution Management

- `[A2A] list` / `[A2A] status` — view progress
- `[A2A] block T2` / `[A2A] unblock T2` — manage blockers
- `[Discuss] T1 dark mode` — session flow discussions

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
| Create | `[A2A] new {project}: {desc}` | owner | TO = orchestrator; verifier in TO or CC; orchestrator+verifier required in members |
| Update | `[A2A] refresh` | owner | add/update members (existing members are not removed), update role_permissions/description; owner-hardcoded, not in role_permissions |
| Approve plan | `[Confirm] plan v{N}` | Owner | TO includes board |
| Approve criteria | `[Confirm] criteria v{N}` | Owner | TO includes board |
| Approve output | `[Confirm] output {board}` | Owner | TO includes board |

### 4.3 Roles and Permissions

| Role | Default Verbs |
|------|--------------|
| **orchestrator** | tasks, create, assign, review, block, unblock, cancel, reassign, edit, deadline, output, notify, members, roles, config, arbitrate, continue, comment, list, show, status, heartbeat |
| **verifier** | verify, approve, reject, output, continue, comment, list, show, roles, members, status, heartbeat |
| **worker** | complete, commit, block, heartbeat, continue, comment, list, show, roles, members, status |
| **owner** | tasks, create, unblock, reassign, reopen, continue, comment, list, show, status, members, roles, heartbeat |

Only the four built-in roles above are supported; custom role names (e.g. `designer`) are rejected. `continue` is the progress-report verb: the assignee reports interim progress on a running task.

### 4.4 Instruction Flow Verbs

All sent to Board address. `board_id` auto-injected.

| Verb | Sender | Description |
|------|--------|-------------|
| `create` | orch, owner | Create tasks |
| `assign` | orch | Assign/Reassign task (params: `{"assignee":"..."}`; the assignee must be a board member) |
| `review` | orch | Set reviewer (params: `{"reviewer":"..."}`) |
| `complete` | worker | Complete task |
| `cancel` | orch | Cancel task |
| `edit` | orch | Edit task |
| `deadline` | orch | Set deadline |
| `reassign` | orch | Reassign |
| `block` / `unblock` | assignee/orch, orch/owner | Block/unblock |
| `verify` / `approve` / `reject` | verifier | Review flow |
| `output` | member granted output (usually verifier) | Output confirmation: submit the deliverable after the task is Done |
| `continue` | assignee | Report interim progress on a running task (params: `{"progress":"..."}`) |
| `reopen` | owner | Reject output, reset tasks |
| `comment` | all | Comment |
| `arbitrate` | orch, verifier | Request arbitration |
| `list` / `show` / `members` / `roles` / `status` / `heartbeat` | — | Queries |

**Notes (verb details):**

- **heartbeat**: assignee-only. The first call moves Ready→Running. Rejected when sent by a non-assignee or when the task is not in Ready/Running.
- **cancel**: valid only for the Blocked state. block → cancel abandons the task.
- **notify** is a permission verb: the group broadcast (`[A2A] notice: {board} {message}`) is sent when the owner runs `[A2A] refresh`; there is no standalone `notify_all` command.

### 4.5 Session Flow

Members email each other + CC Board. FROM and TO must be board members.

**Subject Keywords:**

| Subject | Initiator | Purpose |
|---------|-----------|---------|
| `[Proposal] {board} plan v{N}` | Orchestrator | Plan review |
| `[Report] {board} Phase {N}: {title}` | Orchestrator | Progress report |
| `[Discuss] {Task-ID} {topic}` | All | Task discussion |
| `[Confirm] plan v{N} / criteria v{N} / output {board}` | Owner | Approve plan/criteria/output |
| `[Criteria] {board} criteria v{N}` | Verifier | Criteria confirmation |
| `[Review] {board} {target} {task}` | Worker | Peer review |

### 4.6 Notification Flow

System notifications from Board. Subject prefixed with `[A2A]`.

| Notification | Trigger | Recipient | Body Fields |
|-------------|---------|-----------|-------------|
| `init` | board created | all members | Subject: `[A2A] notice: Board {board} created — {desc}`; project info + member list |
| `invite` | board created (member invited) | invited members | Subject: `[A2A] invite: {board}`; Body: API URL + personal Token |
| `assigned` | create/assign | assignee | task_id, board, title, body, reviewer, created_by |
| `review-needed` | complete (task has a reviewer) | reviewer | task_id, assignee, title, summary + action hint |
| `approved` | approve | assignee | task approved, done |
| `rejected` | reject | assignee | reviewer, reason, revise |
| `blocked` | block | all orchestrators + the blocker | blocker, coordinate |
| `unblocked` | unblock | assignee | unblocker, resume |
| `cancelled` | cancel | assignee | task cancelled |
| `output` | output | Owner | final output, please confirm |
| `comment` | comment | counterpart | commenter, text |
| `notify_all` | refresh | all | custom message (no standalone notify_all command) |
| `arbitrate` | arbitrate | requester (admin recipient pending: no admin field on the board yet) | requester, dispute |

> Every command sent to the board also gets a `[A2A-RESULT] {verb} ok|error` reply (board members only, per the reply policy).

### 4.7 Toolset Guide

| Tool | Parameters | Description |
|------|-----------|-------------|
| `board_task_list` | board ID (+ optional `status`, `assignee`) | List/filter tasks |
| `board_task_show` | task ID (short_id or full id) | Task details + parent summaries |
| `board_members` | board ID (+ optional `email`) | List members |
| `board_roles` | board ID | Role → permitted verbs map |
| `board_status` | board ID | Board goal + per-status pipeline (task count + assignee per status) |
| `board_heartbeat` | task ID (short_id or full id) | Long-task heartbeat (no email) |

> Note: Instruction emails address tasks by short_id (e.g. `T1`); tools also accept the full task id (`t_T1_a3f8c21b`). Board targets are the 20-hex board ID (e.g. `a3f8c21b9d4e73b2f0c1`). The platform registers 6 board tools: status / task_list / task_show / heartbeat / members / roles.

---

## Further Reading

- [Owner Guide](a2a-board-owner-guide.md)
- [Orchestrator Guide](a2a-board-orchestrator-guide.md)
- [Verifier Guide](a2a-board-verifier-guide.md)
- [Worker Guide](a2a-board-worker-guide.md)
