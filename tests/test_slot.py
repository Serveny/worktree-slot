import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "slot.sh"
# Resolve via PATH: on Windows a bare "bash" hits System32\bash.exe (WSL) before Git Bash.
BASH = shutil.which("bash")


class SlotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="slot tests ")
        self.addCleanup(self.temp.cleanup)
        # resolve(): macOS /var is a symlink to /private/var, and slot.sh prints physical paths.
        self.root = Path(self.temp.name).resolve() / "repo with spaces"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Slot Tests")
        self.git("config", "user.email", "slots@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        self.script = self.root / ".agents/skills/worktree-slot/slot.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, self.script)
        (self.root / ".gitignore").write_text(
            ".worktrees/\n.task\ntarget/\nsaves/\n"
        )
        (self.root / "feature.txt").write_text("initial\n")
        self.git("add", ".")
        self.git("commit", "-m", "initial")

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def run_slot(self, *args, cwd=None):
        return subprocess.run(
            [BASH, str(self.script), *args],
            cwd=cwd or self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def claim(self, slug, branch=None):
        args = ["claim", slug]
        if branch is not None:
            args.append(branch)
        result = self.run_slot(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        path = Path(result.stdout.strip())
        self.assertTrue(path.is_absolute())
        self.assertTrue(path.is_dir())
        return path

    def commit_feature(self, slot, text="feature\n"):
        (slot / "feature.txt").write_text(text)
        self.git("add", "feature.txt", cwd=slot)
        self.git("commit", "-m", "feature", cwd=slot)
        return self.git("rev-parse", "HEAD", cwd=slot)

    def install_hook(self, event, body, executable=True, name="10-custom.sh"):
        hook = self.script.parent / "hooks" / event / name
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n")
        hook.chmod(0o755 if executable else 0o644)
        self.git("add", str(hook.relative_to(self.root)))
        self.git("commit", "-m", f"add {event}/{name} hook")

    def test_feature_branch_and_list(self):
        slot = self.claim("microwave-effects")
        self.assertEqual(
            self.git("branch", "--show-current", cwd=slot), "feat/microwave-effects"
        )
        result = self.run_slot("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("branch=feat/microwave-effects", result.stdout)
        self.assertIn("task=microwave-effects", result.stdout)

    def test_custom_branch(self):
        slot = self.claim("microwave-effects", "fix/microwave-effects")
        self.assertEqual(
            self.git("branch", "--show-current", cwd=slot), "fix/microwave-effects"
        )

    def test_claim_hook_receives_context_and_keeps_stdout_path_only(self):
        self.install_hook("post-claim", '''[ -f "$1/.task" ]
printf '%s\\n' "$PWD" "$1" "$2" > .worktrees/hook.log
echo hook-output''')
        result = self.run_slot("claim", "hook-task", "fix/hook-task")
        self.assertEqual(result.returncode, 0, result.stderr)
        slot = self.root / ".worktrees/slot-1"
        self.assertEqual(result.stdout, f"{slot}\n")
        self.assertIn("hook-output", result.stderr)
        self.assertEqual(
            (self.root / ".worktrees/hook.log").read_text().splitlines(),
            [str(self.root), str(slot), "fix/hook-task"],
        )

    def test_claim_hook_runs_again_on_reuse(self):
        self.install_hook(
            "post-claim", 'printf "%s\\n" "$2" >> .worktrees/claims.log'
        )
        slot = self.claim("first")
        result = self.run_slot("finish", str(slot))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.claim("second"), slot)
        self.assertEqual(
            (self.root / ".worktrees/claims.log").read_text().splitlines(),
            ["feat/first", "feat/second"],
        )

    def test_non_executable_hooks_are_skipped(self):
        self.install_hook("post-claim", "exit 99", executable=False)
        self.install_hook("post-finish", "exit 99", executable=False)
        slot = self.claim("no-hooks")
        result = self.run_slot("finish", str(slot))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("hook failed", result.stderr)

    def test_multiple_hooks_run_in_filename_order_and_skip_directories(self):
        for event in ("post-claim", "post-finish"):
            for number in (20, 10):
                self.install_hook(
                    event,
                    f'echo {event}-{number} >> .worktrees/hooks.log',
                    name=f"{number}-custom hook.sh",
                )
            (self.script.parent / "hooks" / event / "00-directory").mkdir()
        slot = self.claim("ordered-hooks")
        result = self.run_slot("finish", str(slot))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / ".worktrees/hooks.log").read_text().splitlines(),
            ["post-claim-10", "post-claim-20", "post-finish-10", "post-finish-20"],
        )

    def test_failed_claim_hook_keeps_slot_reserved(self):
        self.install_hook("post-claim", "exit 7")
        self.install_hook("post-claim", "touch .worktrees/later-hook", name="20-later.sh")
        result = self.run_slot("claim", "failed-hook")
        slot = self.root / ".worktrees/slot-1"
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("remains claimed", result.stderr)
        self.assertTrue((slot / ".task").exists())
        self.assertIn("10-custom.sh", result.stderr)
        self.assertFalse((self.root / ".worktrees/later-hook").exists())
        self.assertEqual(self.git("branch", "--show-current", cwd=slot), "feat/failed-hook")

    def test_finish_hook_runs_after_merge_and_release(self):
        self.install_hook("post-finish", '''[ ! -e "$1/.task" ]
[ -z "$(git -C "$1" branch --show-current)" ]
printf '%s\\n' "$PWD" "$1" "$2" > .worktrees/hook.log
echo "Next action: load the example skill and refresh the main checkout."''')
        slot = self.claim("finish-hook")
        self.commit_feature(slot)
        result = self.run_slot("finish", str(slot))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "feature.txt").read_text(), "feature\n")
        instruction = "Next action: load the example skill and refresh the main checkout."
        self.assertIn(instruction, result.stderr)
        self.assertNotIn(instruction, result.stdout)
        self.assertEqual(
            (self.root / ".worktrees/hook.log").read_text().splitlines(),
            [str(self.root), str(slot), "feat/finish-hook"],
        )

    def test_failed_finish_hook_does_not_undo_merge_or_release(self):
        self.install_hook("post-finish", "exit 7")
        self.install_hook("post-finish", "touch .worktrees/later-hook", name="20-later.sh")
        slot = self.claim("finish-hook")
        self.commit_feature(slot)
        result = self.run_slot("finish", str(slot))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning: post-finish hook failed", result.stderr)
        self.assertIn("10-custom.sh", result.stderr)
        self.assertTrue((self.root / ".worktrees/later-hook").exists())
        self.assertFalse((slot / ".task").exists())
        self.assertEqual((self.root / "feature.txt").read_text(), "feature\n")
        self.assertEqual(self.claim("next"), slot)

    def test_merge_reuse_preserves_branch_and_ignored_files(self):
        slot = self.claim("first")
        commit = self.commit_feature(slot)
        cache = slot / "target/cache"
        cache.parent.mkdir()
        cache.write_text("warm")
        save = slot / "saves/local"
        save.parent.mkdir()
        save.write_text("runtime")
        self.git("merge", "--ff-only", "feat/first")
        result = self.run_slot("finish", str(slot))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((slot / ".task").exists())
        self.assertEqual(self.git("branch", "--show-current", cwd=slot), "")
        self.assertEqual(self.git("rev-parse", "feat/first"), commit)
        self.assertEqual(self.claim("second"), slot)
        self.assertEqual(cache.read_text(), "warm")
        self.assertEqual(save.read_text(), "runtime")
        self.assertEqual(self.git("rev-parse", "feat/first"), commit)

    def test_cherry_pick_finish(self):
        slot = self.claim("cherry")
        commit = self.commit_feature(slot)
        (self.root / "other.txt").write_text("advance main\n")
        self.git("add", "other.txt")
        self.git("commit", "-m", "other")
        self.git("cherry-pick", commit)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), commit)
        result = self.run_slot("finish", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "feat/cherry"), commit)

    def test_missing_marker_does_not_destroy_dirty_work(self):
        slot = self.claim("dirty")
        (slot / ".task").unlink()
        (slot / "feature.txt").write_text("unfinished\n")
        (slot / "untracked.txt").write_text("keep me\n")
        result = self.run_slot("claim", "next")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertEqual((slot / "feature.txt").read_text(), "unfinished\n")
        self.assertEqual((slot / "untracked.txt").read_text(), "keep me\n")

    def test_finish_preserves_semantic_whitespace_differences(self):
        slot = self.claim("spaces")
        self.commit_feature(slot, 'const MESSAGE: &str = "hello world";\n')
        (self.root / "feature.txt").write_text('const MESSAGE: &str = "helloworld";\n')
        self.git("add", "feature.txt")
        self.git("commit", "-m", "different string")
        result = self.run_slot("finish", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((slot / ".task").exists())
        self.assertIn('"hello world"', (slot / "feature.txt").read_text())

    def test_finish_refuses_empty_commit(self):
        slot = self.claim("empty")
        self.git("commit", "--allow-empty", "-m", "empty", cwd=slot)
        result = self.run_slot("finish", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no changes to commit", result.stderr)
        self.assertTrue((slot / ".task").exists())

    def test_binary_asset_cherry_pick_finish(self):
        slot = self.claim("asset")
        (slot / "asset.bin").write_bytes(b"asset\0data\xff")
        self.git("add", "asset.bin", cwd=slot)
        self.git("commit", "-m", "binary asset", cwd=slot)
        commit = self.git("rev-parse", "HEAD", cwd=slot)
        (self.root / "other.txt").write_text("advance main\n")
        self.git("add", "other.txt")
        self.git("commit", "-m", "other")
        self.git("cherry-pick", commit)
        result = self.run_slot("finish", "1")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_different_binary_asset_blocks_finish(self):
        slot = self.claim("asset")
        (slot / "asset.bin").write_bytes(b"feature\0data\xff")
        self.git("add", "asset.bin", cwd=slot)
        self.git("commit", "-m", "binary asset", cwd=slot)
        (self.root / "asset.bin").write_bytes(b"different\0data\xff")
        self.git("add", "asset.bin")
        self.git("commit", "-m", "different asset")
        result = self.run_slot("finish", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("merge conflict", result.stderr)
        self.assertTrue((slot / ".task").exists())

    def test_missing_marker_does_not_destroy_unmerged_commit(self):
        slot = self.claim("unmerged")
        commit = self.commit_feature(slot)
        (slot / ".task").unlink()
        result = self.run_slot("claim", "next")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not merged or cherry-picked", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=slot), commit)
        self.assertEqual(self.git("rev-parse", "feat/unmerged"), commit)

    def test_finish_refuses_dirty_then_releases_after_commit(self):
        slot = self.claim("pending")
        (slot / "untracked.txt").write_text("keep")
        result = self.run_slot("finish", "slot-1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((slot / ".task").exists())
        self.git("add", "untracked.txt", cwd=slot)
        self.git("commit", "-m", "pending", cwd=slot)
        self.assertEqual(self.git("rev-parse", "feat/pending", cwd=slot),
                         self.git("rev-parse", "HEAD", cwd=slot))
        result = self.run_slot("finish", "slot-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((slot / ".task").exists())
        self.assertIn("pending", self.git("log", "-1", "--format=%s"))

    def test_finish_rejects_non_slot_path(self):
        marker = self.root / ".task"
        marker.write_text("protect main")
        result = self.run_slot("finish", str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), "protect main")

    def test_duplicate_and_invalid_branch_do_not_change_slot(self):
        slot = self.claim("unique")
        head = self.git("rev-parse", "HEAD", cwd=slot)
        for args in [
            ("unique",),
            ("bad slug",),
            ("valid", "bad..branch"),
            ("valid", "main"),
        ]:
            with self.subTest(args=args):
                result = self.run_slot("claim", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.git("rev-parse", "HEAD", cwd=slot), head)
                self.assertEqual(
                    self.git("branch", "--show-current", cwd=slot), "feat/unique"
                )

    def test_old_numbered_slot_can_be_reused(self):
        slot = self.root / ".worktrees/slot-1"
        self.git("worktree", "add", "-b", "slot-1", str(slot), "main")
        self.assertEqual(self.claim("new-feature"), slot)
        self.assertEqual(
            self.git("branch", "--show-current", cwd=slot), "feat/new-feature"
        )

    def test_concurrent_claims_get_distinct_slots(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda slug: self.run_slot("claim", slug), ["first", "second"]
                )
            )
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(results[0].stdout, results[1].stdout)

    def test_external_skill_installation_and_invocation_from_slot(self):
        self.install_hook("post-claim", "exit 99")
        external = Path(self.temp.name) / "installed skill"
        external.mkdir()
        script = external / "slot.sh"
        shutil.copy2(SCRIPT, script)
        hook = external / "hooks/post-claim/context.py"
        hook.parent.mkdir(parents=True)
        hook.write_text('''#!/usr/bin/env python3
import sys
from pathlib import Path
with Path(".worktrees/hooks.log").open("a") as log:
    log.write(sys.argv[2] + "\\n")
''')
        hook.chmod(0o755)
        self.script = script
        result = self.run_slot("claim", "external")
        self.assertEqual(result.returncode, 0, result.stderr)
        slot = Path(result.stdout.strip())
        result = subprocess.run(
            [
                BASH,
                str(script),
                "claim",
                "nested",
            ],
            cwd=slot / ".agents",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(Path(result.stdout.strip()), slot)
        self.assertEqual(
            (self.root / ".worktrees/hooks.log").read_text().splitlines(),
            ["feat/external", "feat/nested"],
        )

    def test_unmerged_merge_commit_blocks_claim_reuse(self):
        slot = self.claim("merge")
        self.git("switch", "-c", "side", cwd=slot)
        commit = self.commit_feature(slot)
        self.git("switch", "feat/merge", cwd=slot)
        self.git("merge", "--no-ff", "-m", "merge side", "side", cwd=slot)
        self.git("cherry-pick", commit)
        head = self.git("rev-parse", "HEAD", cwd=slot)
        (slot / ".task").unlink()
        result = self.run_slot("claim", "next")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("merge commits outside main", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=slot), head)

    def test_finish_squashes_multiple_commits_and_releases(self):
        slot = self.claim("multi-commit")
        (slot / "feature.txt").write_text("part 1\n")
        self.git("add", "feature.txt", cwd=slot)
        self.git("commit", "-m", "first commit", cwd=slot)
        (slot / "part2.txt").write_text("part 2\n")
        self.git("add", "part2.txt", cwd=slot)
        self.git("commit", "-m", "second commit", cwd=slot)
        initial_main_commit = self.git("rev-parse", "main")

        result = self.run_slot("finish", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("released", result.stdout)
        self.assertFalse((slot / ".task").exists())

        # Main should have exactly 1 new commit
        new_main_commit = self.git("rev-parse", "main")
        self.assertNotEqual(initial_main_commit, new_main_commit)
        self.assertEqual(self.git("rev-parse", "main~1"), initial_main_commit)
        self.assertEqual(self.git("log", "-1", "--format=%s"), "first commit")
        self.assertIn(
            "* first commit\n* second commit", self.git("log", "-1", "--format=%B")
        )
        self.assertEqual((self.root / "feature.txt").read_text(), "part 1\n")
        self.assertEqual((self.root / "part2.txt").read_text(), "part 2\n")

        # Slot is detached on main
        self.assertEqual(self.git("branch", "--show-current", cwd=slot), "")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=slot), new_main_commit)

    def test_finish_inside_slot_with_custom_message(self):
        slot = self.claim("inside-slot")
        self.commit_feature(slot, "inside feature\n")
        result = self.run_slot("finish", "feat(test): completed inside slot", cwd=slot)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((slot / ".task").exists())
        self.assertEqual(
            self.git("log", "-1", "--format=%s"), "feat(test): completed inside slot"
        )

    def test_finish_no_squash_preserves_commit_history(self):
        slot = self.claim("no-squash-task")
        (slot / "feature.txt").write_text("commit one\n")
        self.git("add", "feature.txt", cwd=slot)
        self.git("commit", "-m", "commit one", cwd=slot)
        (slot / "feature.txt").write_text("commit two\n")
        self.git("add", "feature.txt", cwd=slot)
        self.git("commit", "-m", "commit two", cwd=slot)

        # Test calling from inside the slot directory without slot argument
        result = self.run_slot("finish", "--no-squash", cwd=slot)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((slot / ".task").exists())

        # Main must contain both original commit subjects in history
        main_log = self.git("log", "--oneline", "main")
        self.assertIn("commit one", main_log)
        self.assertIn("commit two", main_log)

    def test_finish_already_integrated_branch_releases_slot(self):
        slot = self.claim("already-integrated")
        self.commit_feature(slot)
        # Fast-forward main manually
        self.git("merge", "--ff-only", "feat/already-integrated")

        result = self.run_slot("finish", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already integrated", result.stderr)
        self.assertIn("released", result.stdout)
        self.assertFalse((slot / ".task").exists())

    def test_finish_conflict_aborts_without_running_finish_hook(self):
        self.install_hook("post-finish", "touch .worktrees/finished")
        slot = self.claim("conflict")
        (slot / "feature.txt").write_text("branch change\n")
        self.git("add", "feature.txt", cwd=slot)
        self.git("commit", "-m", "branch change", cwd=slot)

        (self.root / "feature.txt").write_text("main change\n")
        self.git("add", "feature.txt")
        self.git("commit", "-m", "main change")

        result = self.run_slot("finish", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("merge conflict between feat/conflict and main", result.stderr)
        self.assertFalse((self.root / ".worktrees/finished").exists())
        self.assertTrue((slot / ".task").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_finish_accepts_squash_merge(self):
        slot = self.claim("squash-release")
        (slot / "f1.txt").write_text("f1\n")
        self.git("add", "f1.txt", cwd=slot)
        self.git("commit", "-m", "f1", cwd=slot)
        (slot / "f2.txt").write_text("f2\n")
        self.git("add", "f2.txt", cwd=slot)
        self.git("commit", "-m", "f2", cwd=slot)

        self.git("merge", "--squash", "feat/squash-release")
        self.git("commit", "-m", "squash commit")

        result = self.run_slot("finish", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((slot / ".task").exists())

    def test_discard_cleans_dirty_changes_deletes_branch_and_releases(self):
        slot = self.claim("abandon-task")
        self.commit_feature(slot, "unwanted feature\n")
        (slot / "feature.txt").write_text("dirty edits\n")
        (slot / "garbage.txt").write_text("untracked garbage\n")

        result = self.run_slot("discard", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("discarded and released", result.stdout)
        self.assertFalse((slot / ".task").exists())
        self.assertFalse((slot / "garbage.txt").exists())
        self.assertEqual((slot / "feature.txt").read_text(), "initial\n")

        # Branch feat/abandon-task must be deleted
        branches = self.git("branch").split()
        self.assertNotIn("feat/abandon-task", branches)

        # Slot can immediately be claimed for a new task
        new_slot = self.claim("brand-new")
        self.assertEqual(new_slot, slot)
        self.assertEqual(
            self.git("branch", "--show-current", cwd=slot), "feat/brand-new"
        )

    def test_discard_from_within_slot_without_argument(self):
        self.install_hook("post-finish", "touch .worktrees/finished")
        slot = self.claim("abandon-from-inside")
        self.commit_feature(slot, "work\n")
        result = self.run_slot("discard", cwd=slot)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((slot / ".task").exists())
        self.assertFalse((self.root / ".worktrees/finished").exists())
        branches = self.git("branch").split()
        self.assertNotIn("feat/abandon-from-inside", branches)


if __name__ == "__main__":
    unittest.main()
