---
name: worktree-slot
description: Use only when the user explicitly requests the worktree-slot workflow or an applicable AGENTS.md requires it. Reuse persistent Git worktrees with per-task branches and build caches.
metadata:
  opencode/autoinvoke: "false"
---

# Persistent worktree slots

Installation alone does not enable this workflow; require an explicit user request or an applicable `AGENTS.md` instruction.
Run the bundled `slot.sh` using its installed path, from the target repository or one of its slots. The main checkout must be on local `main`; `.worktrees/` and `.task` must be Git-ignored.
Optional automation lives in `hooks/post-claim/` and `hooks/post-finish/` beside `slot.sh`; add executable scripts there when requested and follow any printed next-action instructions.

## Workflow

### Claim

- For a new task, run `slot.sh claim <task-slug> [branch]` and change to the returned absolute path. Defaults to `feat/<task-slug>` from local `main`.
- For an existing task, run `slot.sh list` and continue in its slot.
- Work and commit in the slot. Report branch, slot path, changes and validation.

### Finish

- When the user says "finish", run `slot.sh finish [--no-squash] [slot] [commit-message]`. This squashes the feature branch into local `main` by default; `--no-squash` preserves commit history. Both checkouts must be clean. Follow script output if any.
- Finish detaches the slot at `main` and removes its `.task` marker. The directory, build cache and feature branch remain; the next claim reuses the slot with a new branch from current local `main`.

### Discard

- Only when the user asks to abandon the work, run `slot.sh discard [slot]`. This deletes uncommitted changes, untracked non-ignored files and the feature branch, then detaches the slot at `main` and releases it.

## Constraints

- Main-checkout edits/commits, merges, pushes, finishes and branch cleanup require explicit user instruction; perform only the requested actions.
- Preserve slot directories and `target/`, even after discard. Preserve feature branches unless explicitly discarded. Never use `git worktree remove` on slots or bypass script refusals with resets, cleaning or `.task` deletion; follow script instructions and report blockers.
- Ignored runtime data persists; check it when it affects tests or previews.
