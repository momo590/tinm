# TINM x Windsurf Integration [BETA]

> **Status:** Schema unverified — not tested against a live Windsurf installation.
> Run `windsurf_hook.sh --debug` to verify the JSON received from Windsurf.

## What it does

Maps Windsurf's Cascade agent hook to TINM's memory pipeline. Every prompt
in Cascade gets captured and stored in your PCP thread, exactly like
Claude Code.

## Install

1. In your project, create the Cascade hook entry point (path TBD per
   Windsurf's hook documentation) that delegates to the TINM bridge:

```bash
#!/bin/bash
exec bash ~/.tinm/source/mvp/hooks/windsurf_hook.sh "$@"
```

2. Make it executable: `chmod +x <your hook path>`

3. Verify the schema (first run):
```bash
echo '{"prompt": "test", "session_id": "s1"}' | bash ~/.tinm/source/mvp/hooks/windsurf_hook.sh --debug
cat /tmp/tinm_windsurf_debug.log
```

4. If the schema doesn't match, look at the logged JSON and update the
   field mapping in `tinm_vendor_adapters.py::VENDORS["windsurf"]`
   (`prompt_fields` / `session_fields` / `transcript_fields`).

## Debug mode

Run with `--debug` to log the full JSON received from Windsurf to
`/tmp/tinm_windsurf_debug.log`. This lets you verify the actual field
names Windsurf / Cascade sends.

## Reporting schema issues

If the adapter doesn't work, open an issue with:
- Your Windsurf version
- The contents of `/tmp/tinm_windsurf_debug.log` (no sensitive data)
- The specific Cascade hook surface you're configuring (beforeAgent,
  onPromptSubmit, etc.)
