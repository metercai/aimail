# Step 7: Patch Hermes — webhook preprocessor
# ═══════════════════════════════════════════════════════════════
if [ -z "${PATCH_STEP_PARENT:-}" ]; then
    step_begin "$T_WEBHOOK"
fi

WEBHOOK_PY="$HERMES_DIR/gateway/platforms/webhook.py"

if [ ! -f "$WEBHOOK_PY" ]; then
    step_warn "$T_WEBHOOK_MISS"
    info "  $T_WEBHOOK_HINT"
else
    if grep -q "PREPROCESS_REGISTRY" "$WEBHOOK_PY" 2>/dev/null; then
        step_ok "$T_WEBHOOK_OK"
    else
        echo -n "  $T_WEBHOOK_APPLY "
        PATCH_OUT=$(python3 "$SCRIPT_DIR/hermes/apply_webhook_patch.py" "$WEBHOOK_PY" 2>&1) && echo "$T_OK" || { echo "$T_FAILED"; echo "$PATCH_OUT" | head -5 | sed 's/^/  | /'; }
        if grep -q "PREPROCESS_REGISTRY" "$WEBHOOK_PY" 2>/dev/null; then
            step_ok "$T_WEBHOOK_DONE"
        else
            step_warn "$T_WEBHOOK_FAIL"
        fi
    fi
fi

# ═══════════════════════════════════════════════════════════════
