# Bootstrap prompt for the VPS Claude Code instance

This is the prompt to paste into a fresh Claude Code session on the
Hostinger VPS. It is self-contained: the VPS Claude has no memory of
the Mac-side conversation, so the prompt carries everything it needs
to act.

Before pasting, replace the two `<...>` placeholders at the top of
the prompt with the real values.

---

## Prompt to paste

```text
You are bootstrapping TINM on this Hostinger VPS as the second host in
a Phase 2 cross-machine setup. The Mac side is already wired and
pushing the PCP store to a private GitHub repo.

CONTEXT VARIABLES (the user filled these in):
- TINM_REPO_URL = <ssh URL of the TINM source repo, e.g. git@github.com:user/TNIM.git>
- TINM_PCP_REPO_URL = <ssh URL of the tinm-pcp repo, e.g. git@github.com:user/tinm-pcp.git>

YOUR JOB, step by step. Run each step, report the result, and STOP if
anything fails — do not skip ahead.

1. Detect the distro: `lsb_release -a` or `cat /etc/os-release`.
   If not Debian/Ubuntu, stop and report — the script below assumes apt.

2. Install system deps:
   sudo apt update
   sudo apt install -y python3.12 python3.12-venv git
   (If python3.12 is not in apt, try python3 and adapt the venv command.)

3. Generate an SSH key if ~/.ssh/id_ed25519 does not already exist:
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "tinm-vps-$(hostname)"

4. Print the public key:
   cat ~/.ssh/id_ed25519.pub
   STOP HERE. Report the public key to the user, and tell them to paste
   it at https://github.com/settings/ssh/new (give it a name like
   "tinm-vps"). Wait for confirmation from the user before resuming.

5. After user confirms the key is added, verify GitHub access:
   ssh -T git@github.com
   Expected output ends with "successfully authenticated".

6. Clone the TINM source repo:
   git clone $TINM_REPO_URL ~/TNIM
   cd ~/TNIM

7. Build the TINM venv + symlinks (mirror of mvp/README.md install):
   mkdir -p ~/.tinm
   python3.12 -m venv ~/.tinm/.venv
   ~/.tinm/.venv/bin/pip install --no-cache-dir -r mvp/requirements.txt
   mkdir -p ~/.claude/skills ~/.claude/commands
   ln -s ~/TNIM/mvp/skill ~/.claude/skills/tinm
   ln -s ~/TNIM/mvp/commands/tinm.md ~/.claude/commands/tinm.md

8. Clone the PCP store from GitHub into $TINM_PCP_DIR:
   The default is ~/.tinm/pcp. Use that.
   git clone $TINM_PCP_REPO_URL ~/.tinm/pcp

9. Wire ~/.claude.json on the VPS. Create or merge the following block
   (replace `<user>` with the actual Linux user — check via `whoami`):

   {
     "mcpServers": {
       "tinm": {
         "command": "/home/<user>/.tinm/.venv/bin/python",
         "args": ["/home/<user>/TNIM/mvp/mcp_server/tinm_server.py"]
       }
     },
     "hooks": {
       "SessionStart": [
         { "hooks": [{ "type": "command",
                       "command": "/home/<user>/TNIM/mvp/hooks/session_start.sh" }] }
       ],
       "UserPromptSubmit": [
         { "hooks": [{ "type": "command",
                       "command": "/home/<user>/TNIM/mvp/hooks/user_prompt.sh" }] }
       ]
     }
   }

   No TINM_PCP_DIR env var is needed if you used the default ~/.tinm/pcp
   — the back-compat resolver in tinm_paths.py finds it automatically.

10. Sanity-check the install:
    ~/.tinm/.venv/bin/python -c "import mcp, sentence_transformers; print('OK')"
    ~/.tinm/.venv/bin/python ~/TNIM/mvp/hooks/session_start.sh
    The second command should print the markdown context of the current
    thread (probably "tinm-paper-polish").

11. Tell the user to fully quit Claude Code on the VPS (Ctrl+D or exit
    twice) and reopen it. From the next session onwards, the
    SessionStart hook will pull from GitHub before injecting context,
    and the UserPromptSubmit hook will push after each turn.

12. Final test: in a new Claude Code session on the VPS, ask Claude to
    run `tinm.artifact_add` registering a small test artifact named
    "Phase 2 VPS first artifact". Then tell the user to verify on the
    Mac (in their session: /tinm load tinm-paper-polish) that the new
    artifact appears. That closes the loop.

End of plan. Begin at step 1.
```

---

## Notes for the user (Mac side, not for the prompt)

- The VPS Claude needs `ANTHROPIC_API_KEY` in its shell env. If you
  haven't set it, do so in `~/.bashrc` on the VPS *before* launching
  Claude Code there: `export ANTHROPIC_API_KEY="..."`. Do not paste the
  key into the bootstrap prompt.
- When the VPS Claude pauses at step 4, you'll need a browser to add
  the SSH key on GitHub. Keep the page
  https://github.com/settings/keys handy.
- If the VPS Claude is on a free / smaller plan and Step 7 times out
  on the `pip install` (sentence-transformers + torch are heavy ~5 min
  on a 1-core VPS), retry that step — the wheels are cached.
