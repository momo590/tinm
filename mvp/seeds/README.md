# `mvp/seeds/` — bundled demo threads

This directory holds pre-built PCP v0 thread seeds that the installer
ships to brand-new users. They exist for one reason: a first-time
installer has an empty `~/.tinm/pcp/` and so cannot personally
experience TINM's cross-session-recall whoa moment until they have
accumulated 24h+ of real usage. The seed lets them feel the moment in
minute 2 instead.

## `tinm-tour/`

A 5-artifact, 8-turn walkthrough of the founder shipping Phase 1 of
the TINM paper (the 2WikiMultihopQA pilot). Real content, real
embeddings.

Files:

- `thread.json`  — PCP v0 trajectory + anchor + top_terms
- `artifacts.json` — 5 artifacts (Pareto plot, wiki2hop results, TINM
  substrate doc, tokens-saved measurement, PCP spec)

Installed by `mvp/skill/tinm_demo.py` (which the user invokes as
`/tinm demo`). Removed by the same script with `--remove`.

## Regenerating the seed

Re-run after editing `build_tinm_tour.py` (e.g. updated text, changed
ALPHA, new embedding model):

```bash
~/.tinm/.venv/bin/python mvp/seeds/build_tinm_tour.py
```

Commit the regenerated `tinm-tour/*.json` to the repo so installers
get the latest version next `curl | bash`.

## Adding a new seed

Copy `build_tinm_tour.py`, change `THREAD_ID`, replace `TURNS` and
`ARTIFACTS` with the new walkthrough material, run it, commit the
output JSON. Update `tinm_demo.py` if you want the user to be able to
pick a seed by name (currently it only ships `tinm-tour`).
