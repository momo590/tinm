"""Canonical regression test for v0.2.2: SessionStart auto-pull must handle
untracked-file collisions between hosts.

Reproduces the bug observed 2026-05-14: host A committed a file at path X
and pushed; host B had X as untracked (independent creation); host B's
session started; `git pull --rebase --autostash` failed silently with
"untracked working tree files would be overwritten by checkout" because
--autostash only stashes TRACKED modifications. The user saw stale state.

The two hosts in this test are generic — any pair of POSIX hosts running
TINM (Mac+Linux, Linux+Linux, WSL+Linux, etc.) exhibit the same behavior.

v0.2.2 fix: stash --include-untracked before pull. This test asserts the fix.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def git(*args, cwd, check=True, capture=True):
    """Run git with isolated identity config. Returns CompletedProcess."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@tinm",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@tinm",
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        capture_output=capture,
        text=True,
        env=env,
    )


def hook_sync_block(pcp_dir: Path, sync_log: Path) -> dict:
    """Replicates the v0.2.2 sync block in session_start.sh.

    Returns a dict with `pull_ok` and `stashed` flags, mirroring what the
    hook logs. If this function and the hook diverge, the test grep at the
    bottom of test_session_start_collision.py catches it.
    """
    stashed = False
    porcelain = git("status", "--porcelain", cwd=pcp_dir, check=False)
    if porcelain.stdout.strip():
        r = git(
            "stash",
            "push",
            "--include-untracked",
            "-m",
            "tinm-session-start-autostash-test",
            "--quiet",
            cwd=pcp_dir,
            check=False,
        )
        if r.returncode == 0:
            stashed = True

    pull = git("pull", "--rebase", "--quiet", cwd=pcp_dir, check=False)
    pull_ok = pull.returncode == 0
    if not pull_ok:
        git("rebase", "--abort", cwd=pcp_dir, check=False)

    if stashed:
        pop = git("stash", "pop", "--quiet", cwd=pcp_dir, check=False)
        if pop.returncode != 0:
            # On pop conflict, reset working tree, preserve work in stash
            git("checkout", "--", ".", cwd=pcp_dir, check=False)
            git("clean", "-fd", cwd=pcp_dir, check=False)

    sync_log.write_text(f"pull_ok={pull_ok} stashed={stashed}\n")
    return {"pull_ok": pull_ok, "stashed": stashed}


class SessionStartCollisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tinm-test-"))
        self.remote = self.tmp / "remote.git"
        self.host_a = self.tmp / "host_a"
        self.host_b = self.tmp / "host_b"
        self.sync_log = self.tmp / "sync-test.log"

        # Bare remote with main as default branch
        git(
            "-c",
            "init.defaultBranch=main",
            "init",
            "--bare",
            "--quiet",
            str(self.remote),
            cwd=self.tmp,
        )

        # host_a clones and pushes initial commit on main
        git(
            "-c",
            "init.defaultBranch=main",
            "clone",
            "--quiet",
            str(self.remote),
            str(self.host_a),
            cwd=self.tmp,
        )
        (self.host_a / "README.md").write_text("initial\n")
        git("checkout", "-b", "main", cwd=self.host_a, check=False)
        git("add", "README.md", cwd=self.host_a)
        git("commit", "--quiet", "-m", "initial", cwd=self.host_a)
        git("push", "--quiet", "-u", "origin", "main", cwd=self.host_a)

        # host_b clones, in sync at this point
        git("clone", "--quiet", str(self.remote), str(self.host_b), cwd=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_untracked_collision_does_not_block_pull(self):
        """The canonical T26 regression: host_a pushes a path that host_b has as
        untracked. Without v0.2.2 fix, pull fails silently. With the fix, pull
        succeeds and host_b gets host_a's commit."""
        # host_a creates and pushes a new file
        (self.host_a / "design.md").write_text("host_a version\n")
        git("add", "design.md", cwd=self.host_a)
        git("commit", "--quiet", "-m", "add design from host_a", cwd=self.host_a)
        git("push", "--quiet", cwd=self.host_a)

        # host_b independently creates the same path as untracked (NOT staged)
        (self.host_b / "design.md").write_text("host_b untracked version\n")

        # Sanity check: this is the exact failure mode without the fix
        legacy_pull = git(
            "pull", "--rebase", "--autostash", "--quiet", cwd=self.host_b, check=False
        )
        self.assertNotEqual(
            legacy_pull.returncode,
            0,
            "Legacy --autostash should FAIL on untracked collision (proves the bug)",
        )

        # Run the v0.2.2 fix
        result = hook_sync_block(self.host_b, self.sync_log)

        # Assertions
        self.assertTrue(result["pull_ok"], "Pull must succeed after v0.2.2 fix")
        self.assertTrue(result["stashed"], "Stash must have been created (dirty repo)")

        log = git("log", "--oneline", cwd=self.host_b).stdout
        self.assertIn(
            "add design from host_a",
            log,
            "host_b must have host_a's commit after sync",
        )

    def test_tracked_modifications_preserved(self):
        """Sanity: tracked modifications should round-trip through stash/pop."""
        # Add and commit a file on both sides via remote sync
        (self.host_a / "shared.md").write_text("v1\n")
        git("add", "shared.md", cwd=self.host_a)
        git("commit", "--quiet", "-m", "add shared.md", cwd=self.host_a)
        git("push", "--quiet", cwd=self.host_a)
        git("pull", "--quiet", cwd=self.host_b)

        # host_b modifies the tracked file
        (self.host_b / "shared.md").write_text("v1\nhost_b edit\n")

        # host_a independently adds a new file and pushes
        (self.host_a / "other.md").write_text("other\n")
        git("add", "other.md", cwd=self.host_a)
        git("commit", "--quiet", "-m", "add other.md", cwd=self.host_a)
        git("push", "--quiet", cwd=self.host_a)

        # Run sync
        result = hook_sync_block(self.host_b, self.sync_log)

        self.assertTrue(result["pull_ok"])
        self.assertTrue(result["stashed"])
        # Tracked modification should be restored after pop
        self.assertEqual(
            (self.host_b / "shared.md").read_text(),
            "v1\nhost_b edit\n",
            "host_b's tracked modification must survive sync",
        )
        # host_a's new file should be present
        self.assertTrue((self.host_b / "other.md").exists())

    def test_clean_repo_no_op_pull(self):
        """When host_b has no local changes, sync is a clean pull with no stash."""
        # host_a pushes a new commit
        (self.host_a / "fresh.md").write_text("fresh\n")
        git("add", "fresh.md", cwd=self.host_a)
        git("commit", "--quiet", "-m", "fresh commit", cwd=self.host_a)
        git("push", "--quiet", cwd=self.host_a)

        # host_b is clean
        self.assertEqual(
            git("status", "--porcelain", cwd=self.host_b).stdout.strip(),
            "",
        )

        result = hook_sync_block(self.host_b, self.sync_log)

        self.assertTrue(result["pull_ok"])
        self.assertFalse(result["stashed"], "No stash should be created on clean repo")
        self.assertTrue((self.host_b / "fresh.md").exists())

    def test_hook_block_matches_implementation(self):
        """Drift detection: the test's hook_sync_block must match what the real
        hook does. Hard to do precisely without parsing bash, but we can at least
        assert the key commands are present in the hook file."""
        hook_path = Path(__file__).resolve().parents[1] / "hooks" / "session_start.sh"
        if not hook_path.exists():
            self.skipTest(f"hook not found at {hook_path}")
        hook_content = hook_path.read_text()
        for required in [
            "stash push --include-untracked",
            "pull --rebase --quiet",
            "stash pop --quiet",
            "checkout -- .",
        ]:
            self.assertIn(
                required,
                hook_content,
                f"Hook must contain '{required}' for v0.2.2 fix to work",
            )


if __name__ == "__main__":
    unittest.main()
