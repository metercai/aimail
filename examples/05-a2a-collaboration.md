# Workflow Collaboration — AgentMail A2A Board

## Scenario
Designer Agent, Frontend Agent, and PM collaborate via A2A Board. All communication and decisions flow through email commands.

## Infrastructure

| Capability | A2A Board |
|-----------|:--:|
| Board creation | `[A2A] new {project}` |
| Task management | `[A2A] create / assign / review` |
| Discussions | Session flow, CC Board injects role context |
| Blocker handling | `[A2A] block / unblock` |
| Auto-notifications | Notification flow (10 event types) |
| Review | `[A2A] approve / reject / output` |

## Flow

```
1. Owner: [A2A] new web-redesign → team formed
2. PM: [Proposal] plan → session flow discussion
3. Owner: [Confirm] plan v1 → approved
4. PM: [A2A] create → tasks T1/T2/T3
5. Designer: [A2A] complete T1 → Worker: [A2A] complete T2
6. QA: [A2A] approve → verified
7. Verifier: [A2A] output → Owner: [Confirm] output
```

## Role Prompts

See `board/role_prompt_en/`:
- `orchestrator.md` — PM rules
- `worker.md` — Designer/Dev rules
- `verifier.md` — QA rules

Full guide: `board/A2A-BOARD-GUIDE.md`
