# Step 8: Patch Hermes — profile hooks
# ═══════════════════════════════════════════════════════════════
if [ -z "${PATCH_STEP_PARENT:-}" ]; then
    step_begin "$T_PROFILES"
fi

PROFILES_PY="$HERMES_DIR/hermes_cli/profiles.py"

if [ ! -f "$PROFILES_PY" ]; then
    # Try alternate path
    PROFILES_PY="$HERMES_DIR/cli/profiles.py"
fi
if [ ! -f "$PROFILES_PY" ]; then
    step_warn "$T_PROFILES_MISS"
    info "  $T_WEBHOOK_HINT"
else
    if grep -q "trigger_profile_hooks" "$PROFILES_PY" 2>/dev/null; then
        step_ok "$T_PROFILES_OK"
    else
        echo -n "  $T_PROFILES_APPLY "
        PATCH_OUT=$(python3 "$SCRIPT_DIR/hermes/apply_profiles_patch.py" "$PROFILES_PY" 2>&1) && echo "$T_OK" || { echo "$T_FAILED"; echo "$PATCH_OUT" | head -5 | sed 's/^/  | /'; }
        if grep -q "trigger_profile_hooks" "$PROFILES_PY" 2>/dev/null; then
            step_ok "$T_PROFILES_DONE"
        else
            step_warn "$T_PROFILES_FAIL"
        fi
    fi

    step_ok "$T_PROFILES_DONE"
fi

# ═══════════════════════════════════════════════════════════════
