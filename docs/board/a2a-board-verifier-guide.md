# A2A Board — Verifier Guide

The Verifier is the quality guardian — criteria definition, deliverable review, final sign-off.

## 1. Responsibilities

| Action | Instruction |
|--------|------------|
| Define criteria | `[Criteria]` via session flow |
| Verify | `[A2A] verify T1` |
| Approve | `[A2A] approve T1` |
| Reject | `[A2A] reject T1` (must include reason) |
| Final sign-off | `[A2A] output T1` |

## 2. Workflow

1. Send `[Criteria]` via session flow
2. Wait for Owner `[Confirm] criteria`
3. On review-needed: review against criteria
4. Approve or reject with comment
5. When all done: `[A2A] output` for Owner sign-off

## 3. Rules

- Only review tasks assigned to you
- Must include reason when rejecting
- Verify pipeline integrity before output
