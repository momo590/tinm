# PCP v0 — Personal Context Protocol, version 0.1

**Status**: draft, MVP.
**Owner**: TINM project.
**Companion docs**: `notes/project_charter.md` §2, `tinm_substrate.md` §7
(compressed latent state $z_t$).

PCP is the file-based wire format that lets multiple LLM clients
(Claude Code, Claude.ai, ChatGPT, OpenClaw, …) share the same TINM
context across sessions and across vendors. PCP is **not** TINM. TINM
is the memory mechanism (anchor + EMA + activation threshold); PCP is
the transport. A client that speaks PCP can read another client's
state and resume where it left off.

This version (`0.1`) is deliberately minimal — file-based, JSON, no
network, no auth. Once the v0.1 contract is stable across two real
clients, v0.2 will tighten compatibility (schema validation, atomic
write semantics, signed updates).

---

## 1. File layout

Per user, two files per work *thread*:

```
~/.tinm/
├── threads/
│   └── <thread_id>.json         # the trajectory + anchor (Section 2)
└── artifacts/
    └── <thread_id>.json         # the L4 conversation-index (Section 3)
```

Split rationale: the trajectory file is the hot path — every user turn
appends ~1 entry, every PCP-aware skill reads it on session start. The
artifact index is colder, larger, and only consulted when the LLM
needs to resolve "the X we did earlier". Splitting avoids re-writing a
500-KB artifact list on every turn.

`thread_id` is a user-chosen slug (lowercase, hyphens, no spaces). It
is the cross-vendor stable identifier. A skill instantiates one
`<thread_id>` per coherent piece of work (e.g. `tinm-paper-polish`,
`q4-board-prep`, `tax-2026`); the same `thread_id` can be opened from
Claude Code today, Claude.ai tomorrow, and ChatGPT on the train.

---

## 2. Trajectory file — `~/.tinm/threads/<thread_id>.json`

### 2.1 Schema

```json
{
  "pcp_version": "0.1",
  "thread_id": "tinm-paper-polish",
  "metadata": {
    "title": "TINM research paper — polish + 2Wiki + n=100",
    "created_at": "2026-05-12T20:58:00Z",
    "last_updated": "2026-05-13T14:23:00Z",
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "embedding_dim": 384,
    "project_root": "/Users/user/TNIM",
    "client_history": ["claude-code"]
  },
  "anchor": {
    "vector": [0.123, -0.045, 0.067, "…"],
    "alpha_used": 0.85,
    "update_count": 12,
    "last_updated_turn": 12,
    "engaged_so_far": true
  },
  "trajectory": [
    {
      "turn": 1,
      "role": "user",
      "text": "Lance les expériences supp: 2Wiki et MuSiQue n=100",
      "ts": "2026-05-12T20:59:14Z",
      "client": "claude-code"
    },
    {"turn": 2, "role": "user", "text": "...", "ts": "...", "client": "..."}
  ]
}
```

### 2.2 Field semantics

| Field | Type | Required | Notes |
|---|---|---|---|
| `pcp_version` | string | yes | Format `MAJOR.MINOR`. Readers MUST refuse files whose `MAJOR` is unknown. |
| `thread_id` | string | yes | Lowercase, hyphens. Stable cross-vendor identifier. |
| `metadata.title` | string | yes | Human-readable label. |
| `metadata.created_at` | ISO 8601 timestamp | yes | UTC. |
| `metadata.last_updated` | ISO 8601 timestamp | yes | UTC. Updated on every write. |
| `metadata.embedding_model` | string | yes | Canonical HF model id. v0.1: `sentence-transformers/all-MiniLM-L6-v2` only. |
| `metadata.embedding_dim` | int | yes | Must equal `len(anchor.vector)` when anchor is initialised. |
| `metadata.project_root` | absolute path | optional | Hint for clients that want to scope to a project. |
| `metadata.client_history` | list[string] | optional | Names of clients that have written to this thread. Appended once on first write per client. |
| `anchor.vector` | list[float] OR null | yes | `null` until first turn writes; thereafter length `= embedding_dim`. |
| `anchor.alpha_used` | float in (0, 1] | yes | The α applied at the last update. Fixed (`0.85`) or adaptive. |
| `anchor.update_count` | int ≥ 0 | yes | Number of EMA updates applied so far. |
| `anchor.last_updated_turn` | int ≥ 0 | yes | Turn index after which the anchor was last updated. |
| `anchor.engaged_so_far` | bool | yes | True once the L1 activation threshold has tripped at least once. Diagnostic. |
| `trajectory` | list[turn] | yes | Append-only. Each entry has `turn` (1-indexed), `role` (`user` or `assistant`), `text`, `ts`, `client`. |

### 2.3 Read/write semantics

- **Read** (idempotent): a client opens the file, parses, uses the
  data. No side effect.
- **Write** (append-update): a client SHOULD perform the
  read–modify–write under a file lock (POSIX `flock` on the file
  descriptor). v0.1 trusts single-user serial access; v0.2 will
  formalise concurrency.
- **Schema mismatch**: if `pcp_version` MAJOR differs from the
  reader's supported MAJOR, the reader MUST stop and surface the
  mismatch (do not silently downgrade).
- **Embedding mismatch**: if `metadata.embedding_model` differs from
  what the reader produces, the reader MUST treat the persisted
  `anchor.vector` as opaque (read but do not blend with a freshly
  computed query embedding); the LLM context can still benefit from
  the trajectory text and the title even if the anchor is dropped.

---

## 3. Artifact file — `~/.tinm/artifacts/<thread_id>.json`

The L4 (implicit references between artifacts) file. Populated by
clients that detect, during a session, references to named pieces of
work — figures, files, results — and want them retrievable on later
turns or by later clients.

### 3.1 Schema

```json
{
  "pcp_version": "0.1",
  "thread_id": "tinm-paper-polish",
  "artifacts": [
    {
      "id": "pareto-plot",
      "name": "Pareto plot",
      "aliases": ["fig 1", "figure 1", "the pareto figure"],
      "turn_first_mentioned": 8,
      "ref": "paper/figures/fig1_pareto.pdf",
      "summary": "Quality vs token cost across 5 benchmarks; tinm_a085 Pareto-best on 4 of 5.",
      "embedding": [0.05, -0.12, "…"],
      "created_at": "2026-05-13T01:33:00Z"
    }
  ]
}
```

### 3.2 Field semantics

| Field | Type | Required | Notes |
|---|---|---|---|
| `artifacts[].id` | string | yes | Slug, stable across renames. |
| `artifacts[].name` | string | yes | Canonical human-readable name. |
| `artifacts[].aliases` | list[string] | optional | Other phrasings the user might use ("the bar chart", "fig 1"). |
| `artifacts[].turn_first_mentioned` | int | yes | For temporal ordering. |
| `artifacts[].ref` | string | yes | A path (relative to `project_root`), a URL, or a free-form locator. |
| `artifacts[].summary` | string | yes | 1–2 sentence description, suitable for LLM context injection. |
| `artifacts[].embedding` | list[float] | optional | If present, length `= embedding_dim` from the trajectory file. Used for semantic lookup. |
| `artifacts[].created_at` | ISO 8601 | yes | UTC. |

### 3.3 Lookup pattern

A client implementing L4 lookup, on detecting an anaphoric reference
in a user query ("modify like we did for the Pareto plot"):

1. Tokenise the query and search `artifacts[].name` + `artifacts[].aliases`
   for direct substring matches (cheap).
2. If no match, encode the query with the same embedding model and
   rank `artifacts[].embedding` by cosine similarity (top-3).
3. Surface the top match(es) — `name`, `ref`, `summary` — to the LLM
   either as context injection or as a tool-call result.

---

## 4. Versioning policy

- `pcp_version` is `MAJOR.MINOR`. MAJOR bumps signal a non-backwards-
  compatible schema change (a reader designed for the old MAJOR
  cannot reliably parse the new one). MINOR bumps add optional fields
  or relax constraints.
- v0.1 is the **MVP contract**. The MVP skill (Claude Code) is the
  first PCP-speaking implementation. Validation = a second client
  (Claude.ai or ChatGPT) can read a thread written by Claude Code and
  resume work intelligibly.
- v0.2 will introduce: atomic write protocol (write-then-rename),
  schema-level validation (`jsonschema`), and explicit conflict
  resolution semantics if two clients race a write.

---

## 5. What v0.1 does NOT do

To keep the MVP tractable, the following are deferred:

- **No encryption / signing.** Threads live in your home directory and
  are trusted. v0.2 may add a signed-write mode for shared threads.
- **No remote sync.** A user with multiple machines must sync
  `~/.tinm/` via existing tools (Syncthing, iCloud Drive, …).
  Cross-machine PCP is a v0.3 problem.
- **No multi-anchor.** v0.1 has one anchor per thread. Multi-scale
  anchors (the substrate doc's §3 SR variants) are research follow-up
  (paper-2), not MVP.
- **No agent-state schema.** L3 (working memory of the agent — open
  files, decisions log, etc.) is explicitly out of v0.1 and is a
  separate research direction (substrate §7, `experimental_roadmap.md`
  §L3).

---

## 6. Reference implementation

The MVP Claude Code skill at `mvp/skill/` is the v0.1 reference
implementation. It exercises:

- Trajectory append-update on every user turn (with L1 activation
  threshold from `benchmark/agents/tinm_lite.py`).
- Artifact extraction triggered manually via `tinm_artifact.py` (v0.1
  does not auto-detect; the user marks artifacts explicitly).
- Anchor blend on retrieval when L1 is engaged.

A second-client validation (e.g., a Claude.ai MCP server that reads
the same files) is the v0.1 acceptance test.

---

## 7. PCP as Hook Interop Standard

### 7.1 PCP beyond TINM-internal storage

PCP v0 was designed as TINM's persistence format, but its thread/artifact
schema is intentionally runtime-agnostic. This section formalises a
consequence of that design: **PCP v0 is also a vendor-neutral hook interop
protocol**.

Every AI coding assistant (Claude Code, Cursor, OpenClaw, …) exposes a
lifecycle-hook mechanism. The hooks differ in name, JSON schema, and
invocation convention — but they all carry the same semantic events:
a user submits a prompt, the agent responds, a tool is called. PCP's
`trajectory` entries are the canonical normalised form of those events.
An "adapter" is a thin shell/Python shim that receives a runtime's native
hook payload and emits a PCP-normalised event into the TINM pipeline.

The result: TINM captures state using the same PCP format regardless of
which runtime the user is working in. A thread started in Claude Code can
be resumed in Cursor or OpenClaw without any schema translation at read
time — because the write-time adapter already normalised the data.

### 7.2 Standard event vocabulary

The table below maps each runtime's native hook name to its PCP-normalised
event tag (stored as `hook_type` in trajectory entries and adapter outputs):

| Runtime | Native hook name | PCP event tag |
|---|---|---|
| Claude Code | `UserPromptSubmit` | `prompt.submit` |
| Claude Code | `Stop` | `agent.response` |
| Claude Code | `PreToolUse` | `tool.before_call` |
| Claude Code | `PostToolUse` | `tool.after_call` |
| Claude Code | `SessionStart` | `session.start` |
| Cursor | `beforeSubmitPrompt` | `prompt.submit` |
| Cursor | `afterAgentResponse` | `agent.response` |
| OpenClaw | incoming message | `prompt.submit` |

Rules:
- A PCP event tag of the form `<noun>.<verb>` is stable across MAJOR versions
  as long as its semantic meaning does not change.
- An adapter that emits an unrecognised event tag MUST prefix it with the
  runtime name (e.g. `cursor.customEvent`) to avoid collisions.
- The `trajectory` entry's `client` field records the runtime name;
  `hook_type` records the PCP event tag. Both are required when written
  by an adapter.

### 7.3 How adapters work

Each runtime has a thin two-layer adapter:

```
[runtime hook] → [shell adapter] → [Python normaliser] → [TINM pipeline]
```

1. **Shell adapter** (e.g. `cursor_hook.sh`): receives the runtime's raw
   invocation (stdin JSON or CLI args), optionally logs it in debug mode,
   locates the TINM venv and Python normaliser, and pipes the payload
   through.

2. **Python normaliser** (e.g. `tinm_cursor.py`): parses the runtime-
   specific JSON, extracts the canonical fields (`prompt`, `session_id`,
   `transcript_path`), and emits a JSON dict that the existing
   `user_prompt.sh` pipeline can consume without modification.

3. **TINM pipeline** (`user_prompt.sh`, `tinm_conv_add.py`, …): unchanged.
   It receives a normalised payload and appends a trajectory entry with
   `client = <runtime>` and the appropriate PCP event tag.

Key properties:
- **Non-blocking**: every adapter exits `0` on any error, so a TINM failure
  never interrupts the user's native workflow.
- **Idempotent reads**: the PCP files written by an adapter are byte-for-byte
  compatible with those written by the Claude Code hooks — same schema,
  same field names.
- **Composable**: a user can run Claude Code and Cursor against the same
  `~/.tinm/threads/<thread_id>.json` file; both adapters append to the same
  trajectory, and `client_history` in `metadata` records which runtimes
  have contributed.

### 7.4 Future: OpenClaw plugin manifest format

OpenClaw exposes an incoming-message hook as part of its plugin manifest.
A future `openclaw_hook.sh` + `tinm_openclaw.py` adapter will follow the
same two-layer pattern. The PCP event tag for OpenClaw incoming messages
is pre-assigned as `prompt.submit` (see table above), so the trajectory
format requires no changes.

The OpenClaw plugin manifest will declare:
```json
{
  "tinm_pcp_version": "0.1",
  "hook": "incoming_message",
  "adapter": "~/.tinm/source/mvp/hooks/openclaw_hook.sh"
}
```

This manifest format is a v0.2 design target; v0.1 OpenClaw integration
uses manual invocation of `openclaw_hook.sh` (identical to the Cursor
pattern).
