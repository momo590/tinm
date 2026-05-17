# TINM x OpenClaw Integration [BETA]

> **Status:** Schema unverified — not tested against a live OpenClaw installation.
> Run `openclaw_hook.sh --debug` to verify the JSON received from OpenClaw.

## What it does

The OpenClaw plugin manifest places this hook to intercept incoming messages
and bridge them to TINM's memory pipeline. Every prompt routed through
OpenClaw (e.g. via a WhatsApp bridge or any other registered client) gets captured and stored in your PCP
thread, exactly like Claude Code.

## Install

1. In your OpenClaw plugin manifest, reference the hook script as the
   incoming-message handler:

```bash
#!/bin/bash
exec bash ~/.tinm/source/mvp/hooks/openclaw_hook.sh "$@"
```

2. Verify the schema (first run):
```bash
echo '{"message": "test", "session_id": "s1"}' | bash ~/.tinm/source/mvp/hooks/openclaw_hook.sh --debug
cat /tmp/tinm_openclaw_debug.log
```

3. If the schema doesn't match, look at the logged JSON and update the
   field mapping in `tinm_vendor_adapters.py::VENDORS["openclaw"]`
   (`prompt_fields` / `session_fields` / `transcript_fields`).

## Debug mode

Run with `--debug` to log the full JSON received from OpenClaw to
`/tmp/tinm_openclaw_debug.log`. This lets you verify the actual field names
OpenClaw sends.

## Reporting schema issues

If the adapter doesn't work, open an issue with:
- Your OpenClaw version / plugin manifest version
- The contents of `/tmp/tinm_openclaw_debug.log` (no sensitive data)
- The specific field names in the payload that we should map
