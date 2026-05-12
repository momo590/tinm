# Working Conventions — TINM project

Binding agreement between the user (project owner) and the Claude agent
collaborating on this codebase. These are operational rules, not preferences.
The agent **must not deviate** without explicit user agreement, even if it
believes deviation would be faster or more efficient.

This document supersedes any earlier informal arrangements. If the agent
finds itself uncertain, it must err on the side of asking the user.

---

## 1. Secret handling

- **Never** request or accept API keys, tokens, or other secrets pasted in
  chat. If the user offers one, the agent must refuse and direct the user
  to set it via shell env (`~/.zshrc`) or a local `.env` file.
- The agent reads `ANTHROPIC_API_KEY` (and any future secrets) from the
  Bash environment only. If a Bash check shows the secret is absent, the
  agent reports this and waits for the user to fix it — never tries to
  work around it.
- The agent never echoes a secret's value (even truncated) to the
  conversation. Length and prefix-of-3 are acceptable for verification.

## 2. Budget guardrails for LLM calls

| Estimated cost | Agent behavior |
|---|---|
| **< $0.50** (smoke tests, dry-runs, single API calls) | Run freely without asking. |
| **$0.50 – $1.00** | Mention the estimate before launching; default proceed unless user objects. |
| **$1.00 – $10** (full pilots, single-script runs) | State the estimate explicitly. **Wait for explicit user confirmation** before launching. |
| **> $10** (large-scale reruns, n>=200, multi-benchmark) | Discuss scope first. **Require explicit "go" from user**. Suggest breaking into smaller staged runs if possible. |

Estimates are always reported with the unit (`$X.Y`), the model used
(`Sonnet 4.6` etc.), and the approximate call count.

The user separately maintains a hard spend limit in the Anthropic console.
The agent treats that as a backstop, not a permission to push to the
limit.

## 3. Execution patterns

- **Smoke before full.** Any new pilot, refactored script, or modified
  benchmark goes through a `--smoke` (n=4) run first. The agent only
  proposes the full run after the smoke succeeds without crashes and
  shows the expected qualitative pattern.
- **Long-running commands go to background.** Anything expected to take
  >2 minutes uses `run_in_background=true` so the agent can continue
  working. Status is reported when the command completes; the agent
  does not poll.
- **Dry-runs without LLM are free and encouraged.** Retrieval-only
  validation, structural checks, and hyperparameter sweeps that don't
  hit the API can run liberally without asking.

## 4. Reproducibility

- **Seed = 42 everywhere by default.** Graph generation, task generation,
  distractor injection, encoder fitting (where applicable). Other seeds
  only when explicitly testing variance.
- **Save results to `benchmark/runs/<name>_results.json`** with a name
  that identifies the experiment. The consolidate script
  (`runs/consolidate.py`) reads these by convention.
- **Never overwrite results without confirmation.** If a run would
  clobber an existing `_results.json`, the agent asks (or writes to a
  versioned name like `_v2_results.json`).
- **Notes go in `/Users/user/TNIM/notes/`**. Major decisions, roadmap
  updates, and conventions live here as markdown.

## 5. Conservatism on destructive operations

- The agent never runs `rm -rf`, `git push --force`, `git reset --hard`,
  `git clean -f`, `git checkout .`, or similar destructive
  git/filesystem commands without explicit user request for the
  specific destructive action.
- File overwrites (`Write` on existing files) are acceptable for
  iteration. The repo is under git (initialized 2026-05-12), so git is
  the safety net.
- Mass refactors that touch >5 files: agent describes the plan and waits
  for confirmation.

### Git workflow specifics
- **Commits only on explicit user request.** The agent does not commit
  proactively after edits, even when many files have changed.
- **Never push without explicit request** (no remote currently set; if
  one is added, this rule applies double for `main`).
- **Never modify git config.**
- **Stage with care.** Prefer adding files by name; `git add .` is only
  acceptable after verifying `git status` shows no sensitive files
  (e.g. `.env`, caches) about to be tracked.
- **Commit messages** describe the *why* in 1-2 sentences, then list
  notable changes. Always include the `Co-Authored-By` trailer.

## 6. Code style

- Python: type hints, dataclasses, no over-engineering. Match the style
  of the existing codebase (`benchmark/`).
- No new dependencies without justification. The current stack is
  `anthropic + sklearn + numpy + datasets`. Adding new packages requires
  a one-line note in the proposal.
- Comments in English (papers are in English; commits and code follow).
  Conversational exchange and documentation pointed at the user can be
  in French where natural.

## 7. Documentation discipline

- After completing a research phase (e.g. a benchmark run + analysis),
  update `notes/experimental_roadmap.md` to reflect status.
- After a significant code change, the agent does not write a CHANGELOG
  or migration doc unless the user requests it. Commit history (or
  conversation) is sufficient for now.
- The agent never creates `.md` documentation files outside of the
  established structure (`notes/`, `paper/`, `runs/paper_results/`)
  without user request.

## 8. Communication style

- The agent is honest about uncertainty, especially around experimental
  results. Statistical significance, sample size, and confounds get
  named explicitly rather than glossed over.
- The agent never agrees with the user's claim if it has reason to
  doubt it. If the user proposes a flawed plan, the agent says so and
  offers an alternative.
- Conversational language can be informal; documents are formal.

## 9. What the agent does NOT decide unilaterally

- Choice of publication venue (the user decides).
- Whether to pursue product or research direction.
- Whether to add or remove agents/benchmarks from the experimental set.
- Any commercial or licensing decisions.

The agent provides analysis and recommendations; the user owns the
strategic decisions.

## 10. Modification of these conventions

Changes to this document require:

1. The user explicitly proposes or accepts the change.
2. The change is recorded with a date in a changelog section below.

The agent **never** modifies these conventions silently or in the course
of executing some other task. If the agent encounters a situation not
covered by these conventions, it asks the user.

---

## Changelog

- **2026-05-12** — Initial version. Established by user during TINM project.
