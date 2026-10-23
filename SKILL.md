---
name: worktree-slot
description: Persistent Git worktree slots with per-task branches and warm build caches. Use only when the user or an applicable AGENTS.md explicitly requests the worktree-slot workflow.
---

Run `slot.sh` from this skill's directory with bash (Windows: Git Bash), inside the target repo or a slot. Requires local `main` checked out in the main checkout and `.worktrees/` + `.task` git-ignored. Obey any refusal or "next action" the script prints; never work around it.

- New task: `slot.sh claim <slug> [branch]` (default `feat/<slug>`), cd to the printed path, work and commit there. Resume: `slot.sh list`.
- On "finish": `slot.sh finish [--no-squash] [slot] [msg]`: squash-merges into `main`, frees the slot, keeps branch and cache.
- Only when asked to abandon: `slot.sh discard [slot]`: deletes uncommitted work and the branch.
- Hooks (only on request): executable scripts in `hooks/post-claim|post-finish/` receive `<slot> <branch>`.
- Without explicit instruction: no edits, commits, merges or pushes on main, no branch deletion.
- Never `git worktree remove` slots, delete `.task`, or reset/clean to bypass refusals. Ignored files (caches, DBs) persist across tasks.
