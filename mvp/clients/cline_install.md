# TINM x Cline Integration [BETA]

> **Status:** Schema unverified — not tested against a live Cline installation.
> Run `cline_hook.sh --debug` to verify the JSON received from Cline.

## What it does

Maps Cline's VS Code extension hook to TINM's memory pipeline. Every prompt
in Cline (formerly Claude Dev) gets captured and stored in your PCP thread,
exactly like Claude Code.

> **Note:** Cline's hook mechanism may require running this script as a
> VS Code task rather than as a native shell hook. See step 1 for the
> tasks.json wiring.

## Install

1. In your project's `.vscode/tasks.json`, add a task that invokes the
   TINM bridge:

```json
{
  "label": "tinm-cline-hook",
  "type": "shell",
  "command": "bash ~/.tinm/source/mvp/hooks/cline_hook.sh",
  "presentation": { "reveal": "never", "panel": "dedicated" }
}
```

   Then wire this task into Cline's notification configuration (see the
   Cline docs for the exact key to bind to user-message events).

2. Verify the schema (first run):
```bash
echo '{"text": "test", "taskId": "t1"}' | bash ~/.tinm/source/mvp/hooks/cline_hook.sh --debug
cat /tmp/tinm_cline_debug.log
```

3. If the schema doesn't match, look at the logged JSON and update the
   field mapping in `tinm_vendor_adapters.py::VENDORS["cline"]`
   (`prompt_fields` / `session_fields` / `transcript_fields`).

## Debug mode

Run with `--debug` to log the full JSON received from Cline to
`/tmp/tinm_cline_debug.log`. This lets you verify the actual field names
Cline sends.

## Reporting schema issues

If the adapter doesn't work, open an issue with:
- Your Cline / VS Code version
- The contents of `/tmp/tinm_cline_debug.log` (no sensitive data)
- The hook surface you're using (VS Code task, extension API, etc.)
