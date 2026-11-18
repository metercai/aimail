# G8 response — activation path boundary

G8's "host-external binding" was a QA fixture shortcut, not a supported
activation path. It is not a product requirement and no headless activation
entry will be added.

Normative rule (contract §5.2a):

- Only an in-host Agent/session tool call (`aimail_system` / `aimail_address`)
  creates a binding.
- External file writes, CLI registration and test harness writes are not
  activation.
- Read-on-boot arming only recovers a binding already created by the in-host
  tool path; it must not be used to justify external activation.

Therefore the G8 acceptance test must not pre-write a binding and then start the
host. It must drive a real host prompt/tool call to activate, then restart and
verify read-on-boot recovery. If the test environment cannot drive an in-host
tool call, it can only test the adapter unit/fixture layer, not acceptance.

OpenClaw `/hooks/agent` is acceptable only if it results in an in-host tool call
to `aimail_address`; it must not write the binding directly from the caller.
