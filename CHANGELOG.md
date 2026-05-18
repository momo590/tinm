# Changelog

All notable changes to TINM are documented here. Format loosely follows
Keep-a-Changelog; versioning is SemVer applied to the on-disk PCP store
and the hook contract.

## [0.3.0] — 2026-05-18

### Performance — hot-path refactor

The headline blocker for v0.3.0 was hook latency. Measured against the
`mvp/scripts/bench_hooks.sh` gate (p99 < 200ms):

| Hook              | v0.2.3 p99 | v0.3.0 p99 | gate |
|-------------------|-----------:|-----------:|-----:|
| SessionStart      |    17.8s* |     106ms |200ms |
| UserPromptSubmit  |    17.8s* |     147ms |200ms |

\* pre-refactor the bench measured `sentence-transformers` cold-import +
all-MiniLM-L6-v2 instantiation on every hook invocation.

Two interleaved fixes:

* **Sentence-transformers off the hot path.** Anchor EMA + embedding now
  run in a fire-and-forget `_anchor_worker.py` subprocess (same pattern
  as the pre-existing `_digest_worker.py`). `tinm_update.update_thread`
  appends the turn, returns the previous turn's pending hint, then
  spawns the worker with `Popen(start_new_session=True)`. The hot path
  has zero `sentence_transformers` imports — asserted by
  `test_tinm_update_async.py`.

* **Hook process-consolidation.** The bash hooks were firing 5–7
  sequential `python -c …` heredocs, paying ~30–40ms of interpreter
  cold-start each. Both hooks now delegate to a single Python entry
  point — `_hot_path_user_prompt.py` / `_hot_path_session_start.py` —
  that does all the lightweight work (JSON parse, conv_add,
  next_action, compaction detect, journal append, write
  `session-<id>.thread`) in one process. Heavy work (score_and_flush,
  push_throttle, anchor worker, digest worker, clipboard daemon) still
  spawns detached via `Popen(start_new_session=True)`.

### Tooling

* `mvp/scripts/bench_hooks.sh` — repeatable p50/p95/p99 measurement
  driver with an isolated `$TINM_HOME`, 5 warmup + 50 measurement runs
  per hook, JSON dump to `benchmark/hook_perf_v0.3.0.json`, exit-code
  gate at 200ms p99.
* `mvp/scripts/release.sh` — `--yes` / `-y` flag for non-interactive
  releases (CI + autopilot).

### Migration

No on-disk schema change vs v0.2.3. Existing PCP stores and
`session-*.thread` handoff files are forward-compatible. The old hook
scripts were drop-in replaced; users who copied the v0.2.x hook bodies
into their own `settings.json` should re-run `mvp/scripts/install.sh`
to pick up the new thin shells.

### Compatibility note

The async worker introduces a one-turn lag for the trajectory hint:
hint shown on turn N+1 reflects the anchor state through turn N. For
sessions of 6+ turns (where L1 engages) this is invisible. If a test
or debug case needs synchronous embedding, `tinm_update.py
--legacy-sync` is preserved as an explicit escape hatch.

## [0.2.3] — 2026-05-17

Thread isolation (DEC-2 freeze-at-start). See `MIGRATION_v023.md`.
