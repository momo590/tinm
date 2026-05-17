#!/usr/bin/env bash
# TINM release cutter — bump VERSION, test, tag, push.
# Reusable for v0.4.0, v0.5.0, … Idempotent: safe to re-run after interrupt.
#
# Usage:
#   ./mvp/scripts/release.sh <new-version> [--skip-bench]
# Example:
#   ./mvp/scripts/release.sh 0.3.0

set -euo pipefail

# ---- colors (with non-tty fallback) ---------------------------------------
if [ -t 1 ] && [ -t 2 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; RESET=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; RESET=""
fi
err()  { printf "%s✗ %s%s\n" "$RED"    "$*" "$RESET" >&2; }
ok()   { printf "%s✓ %s%s\n" "$GREEN"  "$*" "$RESET"; }
warn() { printf "%s! %s%s\n" "$YELLOW" "$*" "$RESET"; }
info() { printf "→ %s\n" "$*"; }

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") <new-version> [--skip-bench]
  new-version   semver x.y.z (e.g. 0.3.0)
  --skip-bench  skip mvp/scripts/bench_hooks.sh latency gate
EOF
    exit 1
}

# ---- 1. parse args --------------------------------------------------------
SKIP_BENCH=0
NEW_VERSION=""
for a in "$@"; do
    case "$a" in
        --skip-bench) SKIP_BENCH=1 ;;
        -h|--help)    usage ;;
        -*)           err "unknown flag: $a"; usage ;;
        *)            [ -z "$NEW_VERSION" ] && NEW_VERSION="$a" || { err "extra arg: $a"; usage; } ;;
    esac
done
[ -n "$NEW_VERSION" ] || usage
if ! printf "%s" "$NEW_VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    err "version must be x.y.z (got: $NEW_VERSION)"; exit 1
fi

# ---- 2. prechecks (fail-fast) ---------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { err "not a git repo"; exit 1; }
cd "$REPO_ROOT"

VERSION_FILE="mvp/VERSION"
TESTS_DIR="mvp/tests"
CHANGELOG="CHANGELOG.md"
BENCH="mvp/scripts/bench_hooks.sh"
TAG="v$NEW_VERSION"
PY="${TINM_VENV:-$HOME/.tinm/.venv}/bin/python3"

[ -f "$VERSION_FILE" ] || { err "$VERSION_FILE not found"; exit 1; }

if [ -n "$(git status --porcelain)" ]; then
    err "working tree not clean — commit/stash first:"
    git status --short >&2
    exit 1
fi

CUR_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
[ "$CUR_BRANCH" = "main" ] || { err "must be on main (currently: ${CUR_BRANCH:-detached})"; exit 1; }

info "fetching origin"
git fetch --quiet origin
LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse origin/main)"
[ "$LOCAL_SHA" = "$REMOTE_SHA" ] || { err "local main diverges from origin/main"; exit 1; }

if git rev-parse "$TAG" >/dev/null 2>&1; then
    err "tag $TAG already exists"; exit 1
fi

OLD_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
if [ "$OLD_VERSION" = "$NEW_VERSION" ]; then
    err "$VERSION_FILE already at $NEW_VERSION — nothing to bump"; exit 1
fi

ok "prechecks passed (current: $OLD_VERSION → new: $NEW_VERSION)"

# ---- 3. bump VERSION ------------------------------------------------------
printf "%s\n" "$NEW_VERSION" > "$VERSION_FILE"
info "VERSION: $OLD_VERSION → $NEW_VERSION"

# Auto-revert on any failure between here and the commit. Cleared after commit.
revert_version() { printf "%s\n" "$OLD_VERSION" > "$VERSION_FILE"; warn "reverted VERSION to $OLD_VERSION"; }
trap 'rc=$?; [ "$rc" -ne 0 ] && revert_version; exit $rc' EXIT

# ---- 4. tests -------------------------------------------------------------
info "running tests"
if [ ! -x "$PY" ]; then
    err "python venv not found at $PY (set TINM_VENV or install first)"; exit 1
fi
if ! "$PY" -m pytest "$TESTS_DIR" -x --tb=line -q; then
    err "tests failed — aborting release"; exit 1
fi
ok "tests pass"

# ---- 5. CHANGELOG entry ---------------------------------------------------
CHANGELOG_TOUCHED=0
if [ -f "$CHANGELOG" ]; then
    if ! grep -Eq "^## \[?${NEW_VERSION}\]?" "$CHANGELOG"; then
        warn "no CHANGELOG entry for $NEW_VERSION"
        if [ -t 0 ]; then
            printf "Open \$EDITOR to add it? [y/N] "
            read -r reply
        else
            reply="n"
        fi
        if [ "$reply" = "y" ] || [ "$reply" = "Y" ]; then
            "${EDITOR:-vi}" "$CHANGELOG"
            CHANGELOG_TOUCHED=1
            grep -Eq "^## \[?${NEW_VERSION}\]?" "$CHANGELOG" \
                || { err "still no entry for $NEW_VERSION after edit"; exit 1; }
        else
            err "CHANGELOG entry required"; exit 1
        fi
    else
        ok "CHANGELOG entry found"
    fi
else
    warn "$CHANGELOG missing — skipping (consider adding one)"
fi

# ---- 6. bench gate --------------------------------------------------------
if [ "$SKIP_BENCH" -eq 1 ]; then
    warn "bench skipped (--skip-bench)"
elif [ -x "$BENCH" ] || [ -f "$BENCH" ]; then
    info "running hook latency bench"
    if ! bash "$BENCH"; then
        err "bench failed (p99 ≥ 200ms gate). Re-run with --skip-bench to override."; exit 1
    fi
    ok "bench passed"
else
    info "no $BENCH — skipping"
fi

# ---- 7. final confirmation ------------------------------------------------
cat <<EOF

Ready to release $TAG:
  VERSION   $OLD_VERSION → $NEW_VERSION
  tests     pass
  CHANGELOG $( [ -f "$CHANGELOG" ] && echo "entry present" || echo "(file absent)" )
  bench     $( [ "$SKIP_BENCH" -eq 1 ] && echo "skipped" || echo "passed/absent" )

Will commit, tag $TAG, and push origin main + tags.
EOF
if [ -t 0 ]; then
    printf "Proceed? [y/N] "
    read -r confirm
else
    confirm="n"
fi
[ "$confirm" = "y" ] || [ "$confirm" = "Y" ] || { err "aborted by user"; exit 1; }

# ---- 8. commit + tag + push -----------------------------------------------
git add "$VERSION_FILE"
[ "$CHANGELOG_TOUCHED" -eq 1 ] && git add "$CHANGELOG"
git commit -m "release: v$NEW_VERSION"
git tag -a "$TAG" -m "Release $TAG"

# Past the revert window — clear the trap.
trap - EXIT

info "pushing main"
git push origin main
info "pushing tag $TAG"
git push origin "$TAG"
ok "pushed $TAG"

# ---- 9. verify upgrade-notify ---------------------------------------------
info "waiting 5s for GitHub raw cache"
sleep 5
UPDATE_CHECK="$REPO_ROOT/mvp/skill/tinm_update_check.py"
if [ -f "$UPDATE_CHECK" ] && [ -x "$PY" ]; then
    if out="$("$PY" "$UPDATE_CHECK" --force 2>&1)" && printf "%s" "$out" | grep -q "$NEW_VERSION"; then
        ok "upgrade-notify advertises $NEW_VERSION"
    else
        warn "upgrade-notify did not report $NEW_VERSION yet (cache propagation can take minutes)"
        printf "%s\n" "$out" | sed 's/^/    /'
    fi
else
    warn "tinm_update_check.py not found — skipping verification"
fi

# ---- 10. success ----------------------------------------------------------
printf "\n%s✓ Released %s%s\n" "$GREEN" "$TAG" "$RESET"
printf "  next: gh release create %s --generate-notes\n" "$TAG"
printf "  next: announce on X / DM beta users\n"
