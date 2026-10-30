# A2A Board — Owner Guide

The Owner is the project sponsor and final decision maker. This guide covers project management via email.

## 1. Your Responsibilities

| Phase | Instruction | Notes |
|-------|------------|-------|
| Form team | `[A2A] new {project}: {desc}` | Send to Orchestrator (TO = orchestrator; verifier in TO or CC). Must include orchestrator+verifier; built-in roles only |
| Approve plan | `[Confirm] plan v{N}` | TO includes board. Auto-sets plan_version/plan_confirmed_at |
| Approve criteria | `[Confirm] criteria v{N}` | TO includes board. Auto-sets criteria_version/criteria_confirmed_at |
| Approve output | `[Confirm] output {board}` | TO board. Project archived as completed |
| Manage members | `[A2A] refresh` | TO board. Update members and permissions |

## 2. Forming a Team

Send to Orchestrator (TO = orchestrator; the verifier must appear in TO or CC; the sender must be declared as an owner member):
```
To: pm@company.com
CC: qa@company.com
Subject: [A2A] new web-redesign: Website Redesign
{"members":[{"email":"owner@company.com","role":"owner","display_name":"Owner"},{"email":"pm@company.com","role":"orchestrator","display_name":"PM"},{"email":"qa@company.com","role":"verifier","display_name":"QA"},{"email":"dev@company.com","role":"worker","display_name":"Dev"}]}
```

Members must include orchestrator and verifier, and may only use the four built-in roles. Sender must be declared as the owner member. `role_permissions` is optional; when provided it is **unioned** with the safe defaults (you can add verbs, never remove them). All members receive the init notification.

## 3. Approving Plans

After Orchestrator publishes [Proposal]:
```
To: pm@company.com, web-redesign.a2a@company.com
Subject: [Confirm] plan v2
Plan v2 approved. Proceed.
```

## 4. Approving Criteria

After Verifier publishes [Criteria]:
```
To: qa@company.com, web-redesign.a2a@company.com
Subject: [Confirm] criteria v1
Criteria v1 approved.
```

## 5. Approving Final Output

After Verifier submits [A2A] output, you receive notification. Confirm:
```
To: web-redesign.a2a@company.com
Subject: [Confirm] output web-redesign
All deliverables approved. Project complete.
```

## 6. Managing Members

Mid-project changes (members are updated incrementally — listed members are added/updated, others are kept; `role_permissions` is unioned with the defaults):
```
To: web-redesign.a2a@company.com
Subject: [A2A] refresh
{"members":[{...}]}
```

## 7. Notifications

| Notification | Meaning | Action |
|-------------|---------|--------|
| `[A2A] notice: Board XXX created — ...` | Team formed | No action — wait for the plan |
| `[A2A] output: XXX T1` | Verifier done | Review, send `[Confirm] output` |
| `[A2A] notice: XXX ...` | Group broadcast (from refresh) | Read and note |
