# Acceptance matrix (QA copy)

| ID | Pass criterion |
|---|---|
| B1 | `claim-base.sh --kind system` prints `sys-...` |
| B2 | `.data/system.json` has `"success": true`, `domain_keys`, default workspace |
| B3 | `.data/address.json` has `"success": true`, `"mode": "code"` |
| B4 | `.data/address2.json` uses `second.test` key and succeeds |
| B5 | `.data/hub.json` has `"followups"` non-empty |
| A1 | `claim-advanced.sh` prints `addr-...` |
| A2 | `.data/adv-address.json` has `"success": true` |
| A3 | `.data/adv-hub.json` has `"followups"` non-empty |
| T1 | `run-tssdk-tests.sh` exits 0 |
| C1 | 3 contract checkers exit 0 |
