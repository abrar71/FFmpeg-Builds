"""Exercise real three-way merges and branch updates using temporary repositories."""

import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

spec = importlib.util.spec_from_file_location("sync_upstream", Path(__file__).with_name("sync_upstream.py"))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class FakeGitHub:
    def __init__(self):
        self.items = []
        self.statuses = []

    def pulls(self):
        return copy.deepcopy(self.items)

    def request(self, method, path, data):
        if path.startswith("statuses/"):
            self.statuses.append(data)
            self.items[0]["head"]["sha"] = path.split('/')[1]
            return {}
        if method == "POST":
            item = dict(data, number=1, state="open", head={"sha": ""}, merged_at=None,
                        html_url="https://github.com/example/repo/pull/1")
            self.items.insert(0, item)
        else:
            self.items[0].update(data)
        return copy.deepcopy(self.items[0])


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.git = sync.Git(self.work)
        self.git.run("init", "-b", "master")
        self.git.run("config", "user.email", "test@example.com")
        self.git.run("config", "user.name", "Test")
        source = Path(__file__).resolve().parents[2]
        self.config = yaml.load((source / sync.WORKFLOW).read_text(), Loader=yaml.BaseLoader)
        self.write_config()
        settings = sync.release_settings((source / sync.WORKFLOW).read_text())
        for target, variant in settings["release combinations"]:
            name, *addins = variant.split()
            self.write(f"variants/{target}-{name}.sh", "# variant\n")
            for addin in addins:
                self.write(f"addins/{addin}.sh", "# addin\n")
        self.write("dependency.txt", "original\n")
        self.initial = self.commit("base")
        self.upstream = self.root / "upstream.git"
        self.origin = self.root / "origin.git"
        self.git.run("clone", "--bare", str(self.work), str(self.upstream))
        self.write("our-riscv-fix.txt", "preserve our fix\n")
        self.local = self.commit("fork fix")
        self.git.run("branch", sync.MIRROR, self.initial)
        self.git.run("clone", "--bare", str(self.work), str(self.origin))
        self.git.run("checkout", "-b", "upstream-work", self.initial)
        self.api = FakeGitHub()

    def write(self, name, text):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def write_config(self):
        self.write(sync.WORKFLOW, yaml.safe_dump(self.config, sort_keys=False))

    def commit(self, message):
        self.git.run("add", ".")
        self.git.run("commit", "-m", message)
        return self.git.text("rev-parse", "HEAD")

    def advance(self, content="updated\n"):
        self.write("dependency.txt", content)
        tip = self.commit("upstream update")
        self.git.run("push", str(self.upstream), "HEAD:master")
        return tip

    def run_sync(self):
        return sync.sync(self.git, self.api, str(self.origin), str(self.upstream))

    def remote(self, branch):
        return sync.Git(self.origin).text("rev-parse", branch)

    def test_clean_merge_idempotence_and_pr_update(self):
        tip = self.advance()
        self.assertIn("PR ready", self.run_sync())
        self.assertEqual(self.remote("master"), self.local)
        self.assertEqual(self.remote(sync.MIRROR), tip)
        self.assertEqual(self.remote(sync.PROPOSAL), tip)
        self.assertEqual(self.api.statuses[-1]["state"], "success")
        self.assertFalse(self.api.items[0]["draft"])
        tree = self.git.text("merge-tree", "--write-tree", self.local, tip)
        self.assertEqual(self.git.text("show", f"{tree}:our-riscv-fix.txt"), "preserve our fix")
        self.run_sync()
        tip = self.advance("another upstream update\n")
        self.run_sync()
        self.assertEqual(len(self.api.items), 1)
        self.assertEqual(self.remote(sync.PROPOSAL), tip)

    def test_already_up_to_date_does_not_create_pr(self):
        self.assertIn("already included", self.run_sync())
        self.assertEqual(self.api.items, [])

    def test_new_cycle_after_merge_and_branch_deletion(self):
        tip = self.advance()
        self.run_sync()
        self.git.run("checkout", "master")
        self.git.run("merge", "--no-ff", "-m", "Accept upstream PR", tip)
        self.git.run("push", str(self.origin), "master", f":{sync.PROPOSAL}")
        self.api.items[0].update(state="closed", merged_at="2026-11-09T12:00:00Z")
        self.git.run("checkout", "upstream-work")
        tip = self.advance("next cycle\n")
        self.run_sync()
        self.assertEqual(len(self.api.items), 2)
        self.assertEqual(self.remote(sync.PROPOSAL), tip)

    def test_matrix_exclusions_and_explicit_inclusions(self):
        matrix = {"target": ["linux", "windows"], "variant": ["gpl", "nonfree"],
                  "exclude": [{"target": "windows", "variant": "nonfree"}],
                  "include": [{"target": "linux", "arch": "arm"},
                              {"target": "windows", "variant": "nonfree", "arch": "x86"}]}
        rows = sync.matrix_rows(matrix)
        self.assertEqual(len(rows), 4)
        self.assertIn({"target": "linux", "variant": "gpl", "arch": "arm"}, rows)
        self.assertIn({"target": "windows", "variant": "nonfree", "arch": "x86"}, rows)

    def test_schedule_change_opens_draft_with_failure(self):
        self.config["on"]["schedule"] = [{"cron": "0 12 * * *"}]
        self.write_config()
        self.advance()
        with self.assertRaisesRegex(RuntimeError, "Changed daily schedule"):
            self.run_sync()
        self.assertTrue(self.api.items[0]["draft"])
        self.assertEqual(self.api.statuses[-1]["state"], "failure")
        self.assertEqual(self.remote("master"), self.local)

    def test_deleted_release_input_fails(self):
        (self.work / "addins/4.4.sh").unlink()
        self.advance()
        with self.assertRaisesRegex(RuntimeError, "Missing release input"):
            self.run_sync()

    def test_removed_release_combination_fails(self):
        self.config["jobs"]["build_ffmpeg"]["strategy"]["matrix"]["target"].remove("linuxriscv64")
        self.write_config()
        self.advance()
        with self.assertRaisesRegex(RuntimeError, "Removed release combinations"):
            self.run_sync()

    def test_conflict_still_opens_reviewable_draft(self):
        self.git.run("checkout", "master")
        self.write("dependency.txt", "our conflicting fix\n")
        self.local = self.commit("our dependency change")
        self.git.run("push", str(self.origin), "master")
        self.git.run("checkout", "upstream-work")
        self.advance()
        with self.assertRaisesRegex(RuntimeError, "Merge conflicts"):
            self.run_sync()
        self.assertIn("dependency.txt", self.api.items[0]["body"])
        self.assertEqual(self.remote("master"), self.local)

    def test_closed_pr_is_not_reopened_for_same_revision(self):
        self.advance()
        self.run_sync()
        self.api.items[0]["state"] = "closed"
        self.assertIn("declined", self.run_sync())
        self.assertEqual(len(self.api.items), 1)
        self.advance("new upstream\n")
        self.run_sync()
        self.assertEqual(len(self.api.items), 2)

    def test_manual_branch_changes_are_not_overwritten(self):
        tip = self.advance()
        self.run_sync()
        self.git.run("checkout", "-b", "manual", tip)
        self.write("manual.txt", "reviewer's work\n")
        manual = self.commit("manual resolution")
        self.git.run("push", str(self.origin), f"HEAD:{sync.PROPOSAL}")
        self.git.run("checkout", "upstream-work")
        self.advance("new update\n")
        with self.assertRaisesRegex(RuntimeError, "diverged"):
            self.run_sync()
        self.assertEqual(self.remote(sync.PROPOSAL), manual)
        self.assertEqual(self.remote(sync.MIRROR), tip)

    def test_upstream_rewrite_is_not_force_pushed(self):
        tip = self.advance()
        self.run_sync()
        self.git.run("reset", "--hard", self.initial)
        self.write("rewrite.txt", "new history\n")
        self.commit("rewritten upstream")
        self.git.run("push", "--force", str(self.upstream), "HEAD:master")
        with self.assertRaisesRegex(RuntimeError, "diverged"):
            self.run_sync()
        self.assertEqual(self.remote(sync.MIRROR), tip)


if __name__ == "__main__":
    unittest.main()
