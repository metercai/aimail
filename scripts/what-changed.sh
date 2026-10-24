#!/usr/bin/env bash
# SDK package "has there been a content change since the last tag" verdict (owner 2026-10-07 policy)
# 2026-10-22: the 5 packages merged into the single agent-aimail — the all-five-bump rule (R2') retired with the merge:
# the single package is judged once; a change ⇒ bump version / enter L2+L3 / release.
set -u
cd "$(dirname "$0")/.." || exit 1
p=agent-aimail
t=$(git tag -l "ts-$p-v*" | sort -V | tail -1)
if [ -z "$t" ]; then echo "$p changed (no tag)"; c=1
elif git diff --quiet "$t" -- "tssdk/packages/$p"; then echo "$p unchanged ($t)"; c=0
else echo "$p changed ($t)"; c=1; fi
if [ "$c" = 1 ]; then echo "VERDICT: agent-aimail changed ⇒ bump version / enter L2+L3 / release"
else echo "VERDICT: agent-aimail unchanged ⇒ SKIP release"; fi
