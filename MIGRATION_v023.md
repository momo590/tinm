# Migrating to TINM v0.2.3

v0.2.3 fixes a structural bug that affected anyone who installed the
bundled `tinm demo` seed and then kept working in Claude Code. This
guide explains what changed, what runs during the migration, and what
to expect afterwards.

## The bug v0.2.3 fixes

Before v0.2.3, `tinm demo` did two things:

1. Wrote the bundled seed thread to `~/.tinm/pcp/threads/tinm-tour.json`.
2. Set `~/.tinm/current_thread` to `tinm-tour` so the demo prompt
   would "just work" in a fresh session.

Subsequent Claude Code sessions then resumed the most recently active
thread — `tinm-tour` — and appended every user prompt to it. If you
worked across multiple projects from non-git directories (or simply
forgot to `tinm init`), weeks of unrelated private work silently piled
onto a thread originally meant to ship as a public demo. The first
attempt to use that seed for an external demo could leak the
accumulated private artifacts.

v0.2.3 closes the bug structurally:

- **Seeds get their own namespace.** Bundled seeds live in
  `~/.tinm/pcp/seeds/` and are tagged `origin: "seed"`. Hooks refuse
  writes to seed threads.
- **The global `current_thread` pointer is gone.** Each session
  resolves a thread from its cwd at SessionStart, freezes that name
  for the session, and writes it to a per-session handoff file
  (`~/.tinm/session-<session_id>.thread`). `cd`'ing mid-session does
  NOT switch threads.
- **Threads carry a workspace fingerprint.** A SHA-256 of the cwd
  realpath is stored on creation. Writes from a different cwd are
  refused unless the user explicitly bridges via
  `tinm thread bridge <slug>`.

Full design rationale: [`design-thread-isolation-2026-05-17.md`](.gstack/projects/tinm/design-thread-isolation-2026-05-17.md).

## What the migration does

Running the migration is a single command:

```bash
python ~/.claude/skills/tinm/../scripts/migrate_v023.py
```

In order:

1. **Tarballs the entire PCP store** to
   `~/.tinm/backups/pcp-pre-v023-<utc-iso8601>.tar.gz`. The rest of
   the script is destructive; backup-first is non-negotiable.
2. **Captures the pre-migration `current_thread` value** to
   `~/.tinm/migration_notes.json` so you can see what your previous
   session pointer was.
3. **Classifies every thread** in `pcp/threads/` by artifact-id set
   intersection against the canonical seed shipped with this install
   (DEC-6 — content hashing would miss the pollution case by
   definition, since a polluted seed has a different content hash):
   - **Pure seed** (artifact IDs match the canonical seed exactly):
     moved to `pcp/seeds/`.
   - **Polluted seed** (canonical IDs ⊂ thread's IDs AND extras): the
     canonical 5 artifacts go to `pcp/seeds/<seed-id>.json`, the
     user-added artifacts go to `pcp/threads/<seed-id>-userfork.json`
     with `origin: "user"`, and the original polluted file is
     quarantined to `~/.tinm/backups/polluted-<id>-<ts>.json`.
   - **Pure user thread** (no overlap with any canonical seed): tagged
     in place with `metadata.origin = "user"`.
4. **Best-effort backfills `workspace_fingerprint`** from
   `metadata.project_root` when that path still resolves on this
   host. Threads without resolvable project_roots are left with
   `workspace_fingerprint: null` and remain writable from any cwd
   (legacy-permissive mode).
5. **Retires `~/.tinm/current_thread`** into the backup directory
   (not permanently deleted — recovery path preserved).
6. **Sweeps stale `~/.tinm/session-*.thread` handoff files** from
   crashed/killed prior sessions, also into the backup directory.
   Without the sweep, the legacy fallback in `user_prompt.sh` would
   keep firing forever on upgraded installs.
7. **Writes `~/.tinm/migration_notes.json`** with a summary you can
   inspect.

Exit codes: `0` success, `1` partial failure (per-thread errors
recorded in notes), `2` already-migrated no-op (re-running is safe).

## What to expect afterwards

- **Your existing threads still work.** Pure user threads are tagged
  in place; their content is unchanged.
- **If you had a polluted `tinm-tour`**, you now have:
  - `~/.tinm/pcp/seeds/tinm-tour.json` — the clean canonical seed.
  - `~/.tinm/pcp/threads/tinm-tour-userfork.json` — your private
    artifacts as their own writable thread. Rename it to anything you
    like with `tinm load tinm-tour-userfork` followed by manual
    file moves; future v0.2.4+ will add a `tinm thread rename`.
- **New threads are per-cwd.** `cd /path/to/projectA && claude` and
  `cd /path/to/projectB && claude` open two distinct threads
  automatically. The previous behaviour of one ambient pointer is
  gone.
- **Working from a second machine on the same project** triggers a
  workspace-fingerprint mismatch. Run `tinm thread bridge <slug>`
  from the second machine's cwd to allow writes from both. Idempotent.

## Inspecting after migration

```bash
cat ~/.tinm/migration_notes.json
ls ~/.tinm/pcp/seeds/             # seeds namespace, read-only
ls ~/.tinm/pcp/threads/           # your user threads
ls ~/.tinm/backups/               # tarball + retired pointers
```

If anything looks wrong, the migration is fully reversible by
unpacking the backup tarball:

```bash
cd ~/.tinm
tar -xzf backups/pcp-pre-v023-<ts>.tar.gz
mv pcp.bak pcp-pre-rollback  # if you want to keep both
```

## When to bridge

`tinm thread bridge <slug>` whenever you see this in your hook log:

```
... session_start gate-refused thread=<slug> cwd=<path> reason=workspace fingerprint mismatch ...
```

The log lives at `~/.tinm/hook-warn-<hostname>.log`. A refusal is
non-fatal — the session stays usable — but writes are blocked until
you bridge. Use `--list` to inspect what's already bridged:

```bash
tinm thread bridge my-project --list
# Thread 'my-project' workspace fingerprints:
#   canonical: sha256:abc...
#   bridges (1):
#     sha256:def...
```

## Reporting issues

Migration is the highest-risk part of v0.2.3. If anything goes wrong:

1. Don't re-run anything — the backup tarball is your recovery path.
2. Open an issue with `~/.tinm/migration_notes.json` attached (it
   never contains thread content, only counts + paths).
3. The tarball at `~/.tinm/backups/pcp-pre-v023-*.tar.gz` is the
   complete pre-migration snapshot; keep it until you're sure.
