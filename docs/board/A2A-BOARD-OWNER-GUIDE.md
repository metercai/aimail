# A2A Board — Owner Guide

The Owner is the project sponsor and final decision maker. This guide covers project management via email.

## 1. Your Responsibilities

| Phase | Instruction | Notes |
|-------|------------|-------|
| Form team | `[A2A] new {project}: {desc}` | Send to Orchestrator. Must include orchestrator+verifier |
| Approve plan | `[Confirm] plan v{N}` | TO includes board. Auto-sets plan_version/plan_confirmed_at |
| Approve criteria | `[Confirm] criteria v{N}` | TO includes board. Auto-sets criteria_version/criteria_confirmed_at |
| Approve output | `[Confirm] output {board}` | TO board. Project archived as completed |
| Manage members | `[A2A] refresh` | TO board. Update members and permissions |

## 2. Forming a Team

Send to Orchestrator:
```
To: pm@company.com
Subject: [A2A] new web-redesign: Website Redesign
{"members":[{"email":"pm@company.com","role":"orchestrator","display_name":"PM"},{"email":"qa@company.com","role":"verifier","display_name":"QA"},{"email":"dev@company.com","role":"worker","display_name":"Dev"}],"role_permissions":[...]}
```

Members must include orchestrator and verifier. Sender must be owner. All members receive init notification.

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

Mid-project changes:
```
To: web-redesign.a2a@company.com
Subject: [A2A] refresh
{"members":[{...}]}
```

## 7. Notifications

| Notification | Meaning | Action |
|-------------|---------|--------|
| `Board XXX created` | Team formed | Wait for plan |
| `output: XXX` | Verifier done | Review, send [Confirm] output |
