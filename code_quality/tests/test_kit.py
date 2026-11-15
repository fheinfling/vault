"""Regression tests for the kit's own scripts. Stdlib only, no model calls.

    python3 -m unittest discover -s code_quality/tests

Each test pins a bug found in review (the integrity detector flagging benign
config and type annotations, the PR template slipping into Tier 0, the eval
runner scoring the historical reviewer) or a policy decision (one family by
default and both on deep, the solo profile, bounded model-lowered Tier 0, and
fixture-based eval cases).
"""

from __future__ import annotations

import json
import argparse
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from unittest import mock
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "scripts"))
sys.path.insert(0, str(KIT / "evals"))

import approval_gate  # noqa: E402
import check_test_integrity as cti  # noqa: E402
import ci_policy  # noqa: E402
import classify_risk  # noqa: E402
import cross_review  # noqa: E402
import evidence_comment  # noqa: E402
import run_evals  # noqa: E402

sys.path.insert(0, str(KIT))
import install  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


class TempRepo:
    """A throwaway git repo whose `origin/main` ref points at the base commit."""

    def __enter__(self) -> "TempRepo":
        self.path = Path(tempfile.mkdtemp(prefix="kit-test-"))
        git(self.path, "init", "-q", "-b", "main")
        git(self.path, "config", "user.email", "t@example.com")
        git(self.path, "config", "user.name", "t")
        return self

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def write(self, rel: str, text: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg: str) -> None:
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", msg)

    def mark_base(self) -> None:
        git(self.path, "update-ref", "refs/remotes/origin/main", "HEAD")

    def integrity_findings(self) -> list[dict]:
        out = subprocess.run([sys.executable, str(KIT / "scripts" / "check_test_integrity.py"), "--base", "origin/main"],
                             cwd=self.path, check=True, capture_output=True, text=True).stdout
        return json.loads(out)["findings"]


class IntegrityDetector(unittest.TestCase):
    def test_benign_config_and_type_annotations_are_not_weakening(self):
        with TempRepo() as r:
            r.write("jest.config.js", 'module.exports = {\n  testEnvironment: "node",\n  testTimeout: 5000,\n};\n')
            r.write("tsconfig.json", '{\n  "compilerOptions": {\n    "checkJs": false\n  }\n}\n')
            r.write("pyproject.toml", '[build-system]\nbuild-backend = "setuptools.build_meta"\n')
            r.write("src/handler.ts", "export function run(x: unknown) {\n"
                                      "  try { return JSON.parse(String(x)); } catch (e) { return null; }\n}\n")
            r.commit("base")
            r.mark_base()
            r.write("jest.config.js", 'module.exports = {\n  testEnvironment: "jsdom",\n  testTimeout: 10000,\n};\n')
            r.write("tsconfig.json", '{\n  "compilerOptions": {\n    "checkJs": true\n  }\n}\n')
            r.write("pyproject.toml", '[build-system]\nbuild-backend = "hatchling.build"\n')
            r.write("src/handler.ts", "export function run(x: unknown) {\n"
                                      "  try { return JSON.parse(String(x)); } catch (e: any) { return { error: e.message }; }\n}\n")
            r.commit("benign")
            self.assertEqual(r.integrity_findings(), [])

    def test_real_weakening_is_still_caught(self):
        with TempRepo() as r:
            r.write("package.json", '{\n  "scripts": {\n    "test": "jest"\n  }\n}\n')
            r.write("src/a.test.ts", "it('adds', () => {\n  expect(add(1, 2)).toBe(3);\n  expect(add(0, 0)).toBe(0);\n});\n")
            r.commit("base")
            r.mark_base()
            r.write("package.json", '{\n  "scripts": {\n    "test": "echo ok"\n  }\n}\n')
            r.write("src/a.test.ts", "it.skip('adds', () => {\n  expect(add(1, 2)).toBe(3);\n});\n")
            r.commit("weaken")
            classes = {f["bug_class"] for f in r.integrity_findings()}
            self.assertTrue({"redefined gate script", "skipped test", "removed assertions"} <= classes, classes)

    def test_gate_script_pattern_matches_script_names_only(self):
        for line in ['  "test": "jest",', '  "test:ci": "jest --ci",', '  "lint:fix": "eslint --fix",',
                     '  "type-check": "tsc --noEmit",', "test:", "build:"]:
            self.assertRegex(line, cti.GATE_SCRIPT)
        for line in ["  testTimeout: 10000,", '  testEnvironment: "jsdom",', '  "checkJs": true,',
                     'build-backend = "hatchling.build"', '  "lint-staged": {', '  "testing-library": "1.0"']:
            self.assertNotRegex(line, cti.GATE_SCRIPT)

    def test_kit_files_are_not_scanned_but_the_repo_still_is(self):
        # Installing or updating the kit adds files full of skip/suppress patterns (they define them).
        with TempRepo() as r:
            r.write("tests/test_app.py", "def test_a():\n    assert 1\n")
            r.commit("base")
            r.mark_base()
            r.write("code_quality/tests/test_kit.py", "import unittest\n@unittest.skip('kit')\ndef test_k():\n    assert 1\n")
            r.write("code_quality/scripts/x.py", "SKIP = '@pytest.mark.skip'  # noqa\n")
            r.write("tests/test_app.py", "import unittest\n@unittest.skip('app')\ndef test_a():\n    assert 1\n")
            r.commit("kit install plus a real skip")
            findings = r.integrity_findings()
            self.assertEqual({f["file"] for f in findings}, {"tests/test_app.py"}, findings)

    def test_type_loosening_is_not_a_suppression(self):
        self.assertNotRegex("} catch (e: any) {", cti.SUPPRESS)
        self.assertNotRegex("const x = y as any;", cti.SUPPRESS)
        self.assertRegex("// eslint-disable-next-line no-console", cti.SUPPRESS)
        self.assertRegex("x = f()  # type: ignore", cti.SUPPRESS)
        self.assertRegex("def f():  # pragma: no cover", cti.SUPPRESS)


class RiskTiers(unittest.TestCase):
    def setUp(self):
        with open(KIT / "quality.toml", "rb") as fh:
            self.cfg = tomllib.load(fh)

    def tier(self, files: list[str]) -> int:
        return classify_risk.deterministic(self.cfg, files, {f: (5, 1) for f in files})[0]

    def test_pr_template_is_part_of_the_quality_system(self):
        self.assertEqual(self.tier([".github/PULL_REQUEST_TEMPLATE.md"]), 2)
        self.assertEqual(self.tier([".github/pull_request_template.md"]), 2)
        self.assertEqual(self.tier([".github/PULL_REQUEST_TEMPLATE/feature.md"]), 2)

    def test_docs_only_change_is_tier0(self):
        self.assertEqual(self.tier(["docs/guide.md", "README.md"]), 0)

    def test_code_in_a_docs_folder_is_not_tier0(self):
        self.assertEqual(self.tier(["docs/server.py"]), 1)

    def test_floor_beats_t0_file_type(self):
        self.assertEqual(self.tier(["src/auth/README.md"]), 2)
        self.assertEqual(self.tier(["prompts/system.md"]), 1)

    def test_floor_exclude_carves_paths_out(self):
        self.cfg["risk"]["floor"] = [{"tier": 2, "reason": "all but docs and tests", "paths": ["**"],
                                      "exclude": ["**/*.md", "Tests/**"]}]

        def tier_and_floor_hit(files):
            tier, _, floor_hit = classify_risk.deterministic(self.cfg, files, {f: (1, 1) for f in files})
            return tier, floor_hit
        for path in [".gitignore", "sub/.gitignore", ".gitattributes", "reference/x.py", "Makefile"]:
            self.assertEqual(tier_and_floor_hit([path]), (2, True), path)
        self.assertEqual(tier_and_floor_hit(["README.md"]), (0, False))
        self.assertEqual(tier_and_floor_hit(["Tests/FooTests.swift"]), (1, False))
        self.assertEqual(tier_and_floor_hit(["Tests/FooTests.swift", ".gitignore"]), (2, True))


class EvalRunner(unittest.TestCase):
    def test_install_kit_runs_from_outside_the_worktree_and_overlays_policy(self):
        wt = Path(tempfile.mkdtemp(prefix="kit-test-wt-"))
        kit_dir = None
        try:
            (wt / "code_quality" / "scripts").mkdir(parents=True)
            (wt / "code_quality" / "scripts" / "review_loop.sh").write_text("# historical script under review\n")
            (wt / "quality.toml").write_text("# historical policy\n")
            (wt / "REVIEW.md").write_text("# historical rubric\n")
            kit_dir = run_evals.install_kit(wt)
            # The reviewer that runs is today's, staged outside the worktree.
            self.assertFalse(kit_dir.is_relative_to(wt))
            self.assertEqual((kit_dir / "scripts" / "cross_review.py").read_text(),
                             (KIT / "scripts" / "cross_review.py").read_text())
            # The historical kit stays in the worktree untouched: it is code under review.
            self.assertEqual((wt / "code_quality" / "scripts" / "review_loop.sh").read_text(),
                             "# historical script under review\n")
            for name in run_evals.POLICY_FILES:
                current = KIT.parent / name
                if current.is_file():
                    self.assertEqual((wt / name).read_text(), current.read_text())
                else:
                    self.assertFalse((wt / name).exists(), f"historical {name} must not outlive the install")
        finally:
            shutil.rmtree(wt, ignore_errors=True)
            if kit_dir is not None:
                shutil.rmtree(kit_dir.parent, ignore_errors=True)



class ReviewRouting(unittest.TestCase):
    def setUp(self):
        with open(KIT / "quality.toml", "rb") as fh:
            self.cfg = tomllib.load(fh)

    def test_one_family_by_default_and_both_on_deep(self):
        self.assertEqual(cross_review.families(self.cfg), ["claude"])
        self.assertEqual(cross_review.families(self.cfg, "standard"), ["claude"])
        self.assertEqual(cross_review.families(self.cfg, "quick"), ["claude"])
        self.assertEqual(cross_review.families(self.cfg, "deep"), ["claude", "codex"])

    def test_deep_falls_back_to_families_when_unset(self):
        cfg = {"review": {"families": ["codex"]}}
        self.assertEqual(cross_review.families(cfg, "deep"), ["codex"])

    def test_unknown_family_names_are_rejected(self):
        with self.assertRaises(SystemExit):
            cross_review.families({"review": {"families": ["gemini"]}})


class SoloProfile(unittest.TestCase):
    def test_differs_from_team_policy_in_exactly_the_intended_values(self):
        with open(KIT / "quality.toml", "rb") as fh:
            team = tomllib.load(fh)
        with open(KIT / "profiles" / "solo.toml", "rb") as fh:
            solo = tomllib.load(fh)
        self.assertEqual(solo["risk"]["approvals"], {"t0": 0, "t1": 1, "t2": 1})
        self.assertTrue(solo["risk"]["allow_model_t0"])
        # Neutralise the two intended differences, then everything else must be identical.
        solo["risk"]["approvals"] = team["risk"]["approvals"]
        solo["risk"]["allow_model_t0"] = team["risk"]["allow_model_t0"]
        self.assertEqual(solo, team)


class ModelLoweredTier0(unittest.TestCase):
    def setUp(self):
        with open(KIT / "quality.toml", "rb") as fh:
            self.cfg = tomllib.load(fh)
        self.cfg["risk"]["allow_model_t0"] = True
        self.ok = {"tier": 0, "reasons": ["additive, behind flag X (default off at config/flags.py:4)"],
                   "needs_human_regardless": False}

    def decide(self, model, floor=1, floor_hit=False, lines=40, allow=True):
        self.cfg["risk"]["allow_model_t0"] = allow
        return classify_risk.decide_tier(self.cfg, floor, floor_hit, model, lines)

    def test_lowers_only_when_every_condition_holds(self):
        tier, reasons, model_t0 = self.decide(self.ok)
        self.assertEqual((tier, model_t0), (0, True))
        self.assertTrue(any("lowered by classifier" in r for r in reasons))

    def test_never_below_a_floor(self):
        self.assertEqual(self.decide(self.ok, floor=2, floor_hit=True)[0], 2)

    def test_off_by_default_policy_keeps_tier(self):
        self.assertEqual(self.decide(self.ok, allow=False)[0], 1)

    def test_size_cap_applies(self):
        limit = self.cfg["risk"]["t0"]["max_changed_lines"]
        tier, reasons, model_t0 = self.decide(self.ok, lines=limit + 1)
        self.assertEqual((tier, model_t0), (1, False))
        self.assertTrue(any("exceed the T0 limit" in r for r in reasons))

    def test_needs_human_regardless_wins(self):
        self.assertEqual(self.decide({**self.ok, "needs_human_regardless": True})[0], 1)

    def test_model_can_always_raise(self):
        self.assertEqual(self.decide({"tier": 2, "reasons": ["touches shared serializer used by authz"],
                                      "needs_human_regardless": False}, floor=1, allow=False)[0], 2)


class FixtureCases(unittest.TestCase):
    def test_fixture_becomes_two_commits_the_detector_can_diff(self):
        case = json.loads((KIT / "evals" / "cases" / "adversarial-skipped-test" / "case.json").read_text())
        repo, base, head = run_evals.materialize_fixture(case["fixture"])
        try:
            self.assertNotEqual(base, head)
            out = subprocess.run([sys.executable, str(KIT / "scripts" / "check_test_integrity.py"), "--base", base],
                                 cwd=repo, check=True, capture_output=True, text=True).stdout
            classes = {f["bug_class"] for f in json.loads(out)["findings"]}
            self.assertIn("skipped test", classes)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_head_null_deletes_a_file(self):
        repo, base, head = run_evals.materialize_fixture({"base": {"a.txt": "a\n", "b.txt": "b\n"}, "head": {"b.txt": None}})
        try:
            self.assertFalse((repo / "b.txt").exists())
            self.assertTrue((repo / "a.txt").exists())
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_internal_cases_are_detected_in_the_kit_source(self):
        if not (KIT / "evals" / "cases" / "clean-kit-fix-2026-11-13").exists():
            self.skipTest("installed copy: source-only cases are not shipped")
        self.assertIn("clean-kit-fix-2026-11-13", install.internal_cases())

    def test_seed_cases_are_well_formed(self):
        cases = run_evals.load_cases(str(KIT / "evals" / "cases"))
        self.assertGreaterEqual(len(cases), 8)
        for c in cases:
            with self.subTest(case=c["id"]):
                self.assertEqual(c["id"], c["_dir"].name)
                self.assertIn(c["kind"], {"incident", "clean", "adversarial", "rule"})
                self.assertIsInstance(c["expect"], list)
                if "fixture" in c:
                    self.assertTrue(c["fixture"]["base"], "fixture needs base files")
                else:
                    self.assertTrue(run_evals.resolve_repo(c).is_dir(), "repo path must exist")
                    for key in ("base", "head"):
                        subprocess.run(["git", "-C", str(run_evals.resolve_repo(c)), "cat-file", "-e", c[key]], check=True)
                for e in c["expect"]:
                    self.assertIn("file", e)
                    self.assertIn(e.get("severity", "important"), {"important", "nit", "pre_existing"})
                if c["kind"] == "clean":
                    self.assertEqual(c["expect"], [])
                else:
                    self.assertTrue(c["expect"], "non-clean cases must expect at least one finding")
                if c["kind"] == "incident" and "fixture" in c:
                    for e in c["expect"]:  # the bug must actually be in the diff
                        self.assertNotEqual(c["fixture"]["base"].get(e["file"]), c["fixture"]["head"].get(e["file"]))
                    self.assertIn("note", c, "incident cases record who adjudicated them and how they were rebuilt")



class InstallerTests(unittest.TestCase):
    def install(self, repo: Path, profile: str) -> str:
        return subprocess.run([sys.executable, str(KIT / "install.py"), str(repo), "--profile", profile],
                              check=True, capture_output=True, text=True).stdout

    def test_fresh_solo_install_creates_everything_and_is_idempotent(self):
        with TempRepo() as r:
            r.write("README.md", "# target\n")
            r.commit("base")
            out = self.install(r.path, "solo")
            for rel in ("code_quality/scripts/cross_review.py", "code_quality/install.py", "quality.toml", "AGENTS.md",
                        "CLAUDE.md", "REVIEW.md", ".claude/agents/bug-hunter.md", ".claude/agents/verifier.md",
                        ".claude/agents/risk-classifier.md", ".github/workflows/quality-gate.yml",
                        ".github/workflows/quality-gate-review.yml", ".github/workflows/quality-gate-refresh.yml",
                        ".github/PULL_REQUEST_TEMPLATE.md", ".claude/settings.json", ".gitignore"):
                self.assertTrue((r.path / rel).exists(), rel)
            for name in ("quality-gate.yml", "quality-gate-review.yml", "quality-gate-refresh.yml"):
                self.assertEqual((r.path / ".github/workflows" / name).read_bytes(),
                                 (KIT / "ci" / name).read_bytes())
            self.assertFalse((r.path / ".github" / "CODEOWNERS").exists(), "solo profile does not install CODEOWNERS")
            with open(r.path / "quality.toml", "rb") as fh:
                self.assertEqual(tomllib.load(fh)["risk"]["approvals"]["t2"], 1)
            self.assertIn(".quality/", (r.path / ".gitignore").read_text())
            self.assertIn("Still to do", out)
            # The seed eval cases travel with the kit; results and caches do not, and neither do the
            # cases that review the kit's own history (their commits exist only there).
            self.assertTrue((r.path / "code_quality" / "evals" / "cases" / "adversarial-skipped-test" / "case.json").exists())
            self.assertFalse((r.path / "code_quality" / "evals" / "results").exists())
            for name in install.internal_cases():
                self.assertFalse((r.path / "code_quality" / "evals" / "cases" / name).exists(), name)
            installed = run_evals.load_cases(str(r.path / "code_quality" / "evals" / "cases"))
            for c in installed:
                self.assertIn("fixture", c, f"installed case {c['id']} must be self-contained")
            snapshot = {p.relative_to(r.path): p.read_bytes() for p in r.path.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}
            self.install(r.path, "solo")
            again = {p.relative_to(r.path): p.read_bytes() for p in r.path.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}
            self.assertEqual(snapshot, again, "second run must change nothing")

    def test_update_keeps_repo_owned_files_and_own_eval_cases(self):
        with TempRepo() as r:
            r.write("CLAUDE.md", "# my repo rules\n")
            r.write("quality.toml", 'version = 1\n[checks]\nfast = ["true"]\nfull = ["true"]\n')
            r.write(".gitignore", "node_modules/\n.quality/\n")
            r.commit("base")
            self.install(r.path, "team")
            own_case = r.path / "code_quality" / "evals" / "cases" / "incident-mine" / "case.json"
            own_case.parent.mkdir(parents=True)
            own_case.write_text("{}\n")
            for name in install.internal_cases():  # simulate an install by an older kit version
                old = r.path / "code_quality" / "evals" / "cases" / name / "case.json"
                old.parent.mkdir(parents=True, exist_ok=True)
                old.write_text("{}\n")
            stale = r.path / "code_quality" / "scripts" / "old_script.py"
            stale.write_text("# removed upstream\n")
            # Upgrade an install that predates the relay and refresh workflows.
            (r.path / ".github/workflows/quality-gate-refresh.yml").unlink()
            (r.path / ".github/workflows/quality-gate-review.yml").unlink()
            (r.path / ".github/workflows/quality-gate.yml").write_text("# old workflow\n")
            self.install(r.path, "team")
            for name in ("quality-gate.yml", "quality-gate-review.yml", "quality-gate-refresh.yml"):
                self.assertEqual((r.path / ".github/workflows" / name).read_bytes(),
                                 (KIT / "ci" / name).read_bytes())
            self.assertEqual((r.path / "CLAUDE.md").read_text(), "# my repo rules\n")
            self.assertEqual((r.path / "quality.toml").read_text(), 'version = 1\n[checks]\nfast = ["true"]\nfull = ["true"]\n')
            self.assertEqual((r.path / ".gitignore").read_text().count(".quality/"), 1)
            self.assertTrue(own_case.exists(), "the repo's own eval cases survive an update")
            self.assertFalse(stale.exists(), "kit files removed upstream are removed on update")
            # A source-only case that an older installer copied in is removed on update.
            for name in install.internal_cases():
                self.assertFalse((r.path / "code_quality" / "evals" / "cases" / name).exists(), name)
            self.assertTrue((r.path / ".github" / "CODEOWNERS").exists())

    def test_update_overwrites_the_kit_owned_refresh_workflow(self):
        with TempRepo() as r:
            r.write(".github/workflows/quality-gate-refresh.yml", "# old refresh workflow\n")
            self.install(r.path, "solo")
            self.assertEqual((r.path / ".github/workflows/quality-gate-refresh.yml").read_bytes(),
                             (KIT / "ci/quality-gate-refresh.yml").read_bytes())

    def test_settings_merge_keeps_existing_and_is_idempotent(self):
        kit = json.loads((KIT / "hooks" / "settings.json").read_text())
        kit.pop("$comment", None)
        existing = {"permissions": {"allow": ["Bash(npm test:*)"], "deny": ["Read(./secrets/**)"]},
                    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
        merged = install.merge_settings(existing, kit)
        self.assertEqual(merged["permissions"]["allow"], ["Bash(npm test:*)"])
        self.assertIn("Read(./secrets/**)", merged["permissions"]["deny"])
        self.assertIn("Read(./.env)", merged["permissions"]["deny"])
        cmds = [h["command"] for e in merged["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertEqual(cmds[0], "echo mine")
        self.assertTrue(any("protect_paths.py" in c for c in cmds))
        self.assertTrue(any("quality_gate.sh" in h["command"] for e in merged["hooks"]["Stop"] for h in e["hooks"]))
        self.assertEqual(install.merge_settings(merged, kit), merged)

    def test_update_widens_the_matcher_of_an_installed_hook(self):
        kit = json.loads((KIT / "hooks" / "settings.json").read_text())
        kit.pop("$comment", None)
        old = json.loads(json.dumps(kit))
        for e in old["hooks"]["PreToolUse"]:
            e["matcher"] = "Edit|Write|MultiEdit|NotebookEdit|Bash"
        mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        old["hooks"]["PreToolUse"].append(mine)
        merged = install.merge_settings(old, kit)
        guard = [e for e in merged["hooks"]["PreToolUse"] if any("protect_paths" in h["command"] for h in e["hooks"])]
        self.assertEqual(len(guard), 1)
        self.assertIn("mcp__", guard[0]["matcher"])
        self.assertIn(mine, merged["hooks"]["PreToolUse"])
        self.assertEqual(install.merge_settings(merged, kit), merged)

    def test_refuses_a_directory_that_is_not_a_git_root(self):
        d = Path(tempfile.mkdtemp(prefix="kit-test-nogit-"))
        try:
            res = subprocess.run([sys.executable, str(KIT / "install.py"), str(d)], capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("not a git repository", res.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TrustedKit(unittest.TestCase):
    SCRIPT = KIT / "scripts" / "trusted_kit.sh"

    def stage(self, repo: Path, base: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.SCRIPT), base, "_trusted"], cwd=repo, check=True,
                              capture_output=True, text=True)

    def test_base_copy_wins_over_the_pr_copy(self):
        with TempRepo() as r:
            r.write("code_quality/scripts/qlib.py", "# base qlib\n")
            r.write("code_quality/quality.toml", "version = 1\n")
            r.write("quality.toml", "version = 1\n# base\n")
            r.commit("base")
            base = git(r.path, "rev-parse", "HEAD").strip()
            r.write("quality.toml", "version = 1\n# tampered by the PR\n")
            r.write("code_quality/scripts/qlib.py", "# PR qlib\n")
            r.commit("pr")
            out = self.stage(r.path, base).stdout
            self.assertEqual((r.path / "_trusted" / "quality.toml").read_text(), "version = 1\n# base\n")
            self.assertEqual((r.path / "_trusted" / "code_quality" / "scripts" / "qlib.py").read_text(), "# base qlib\n")
            self.assertFalse((r.path / "_trusted" / "BOOTSTRAP").exists())
            self.assertNotIn("::warning", out)

    def test_bootstrap_uses_the_pr_copy_and_marks_it(self):
        with TempRepo() as r:
            r.write("README.md", "# no kit yet\n")
            r.commit("base")
            base = git(r.path, "rev-parse", "HEAD").strip()
            r.write("code_quality/scripts/qlib.py", "# PR qlib\n")
            r.write("code_quality/quality.toml", "version = 1\n# kit copy\n")
            r.commit("install the kit")
            out = self.stage(r.path, base).stdout
            self.assertTrue((r.path / "_trusted" / "BOOTSTRAP").exists())
            self.assertIn("::warning", out)
            # No root policy in the PR either: the kit's own copy is the fallback.
            self.assertEqual((r.path / "_trusted" / "quality.toml").read_text(), "version = 1\n# kit copy\n")
            policy = ci_policy.read(str(r.path / "_trusted" / "quality.toml"))
            self.assertTrue(policy["bootstrap"])


    def test_kit_upgrade_runs_under_the_new_scripts_and_is_marked(self):
        with TempRepo() as r:
            r.write("code_quality/scripts/qlib.py", "# v1 qlib\n")
            r.write("code_quality/quality.toml", "version = 1\n")
            r.write("quality.toml", "version = 1\n# base policy\n")
            r.commit("old kit, no KIT_VERSION")
            base = git(r.path, "rev-parse", "HEAD").strip()
            r.write("code_quality/scripts/qlib.py", "# v2 qlib\n")
            r.write("code_quality/KIT_VERSION", "2\n")
            r.write("quality.toml", "version = 1\n# PR tries to bring its own policy\n")
            r.commit("upgrade")
            out = self.stage(r.path, base).stdout
            self.assertEqual((r.path / "_trusted" / "code_quality" / "scripts" / "qlib.py").read_text(), "# v2 qlib\n")
            self.assertIn("version 1", (r.path / "_trusted" / "BOOTSTRAP").read_text())
            # The scripts are the PR's; the policy stays the base's.
            self.assertEqual((r.path / "_trusted" / "quality.toml").read_text(), "version = 1\n# base policy\n")
            self.assertIn("::warning", out)

    def test_unparsable_version_counts_as_an_upgrade(self):
        with TempRepo() as r:
            r.write("code_quality/scripts/qlib.py", "# base\n")
            r.write("code_quality/quality.toml", "version = 1\n")
            r.write("code_quality/KIT_VERSION", "2\n")
            r.commit("base")
            base = git(r.path, "rev-parse", "HEAD").strip()
            r.write("code_quality/KIT_VERSION", "x\n")
            r.commit("garbage version")
            self.stage(r.path, base)
            self.assertTrue((r.path / "_trusted" / "BOOTSTRAP").exists())

    def test_same_version_keeps_the_base_copy(self):
        with TempRepo() as r:
            r.write("code_quality/scripts/qlib.py", "# base qlib\n")
            r.write("code_quality/quality.toml", "version = 1\n")
            r.write("code_quality/KIT_VERSION", "2\n")
            r.commit("base")
            base = git(r.path, "rev-parse", "HEAD").strip()
            r.write("code_quality/scripts/qlib.py", "# PR qlib\n")
            r.commit("pr edits a script without bumping the version")
            self.stage(r.path, base)
            self.assertEqual((r.path / "_trusted" / "code_quality" / "scripts" / "qlib.py").read_text(), "# base qlib\n")
            self.assertFalse((r.path / "_trusted" / "BOOTSTRAP").exists())


class EvidenceHook(unittest.TestCase):
    """protect_paths.py keeps the agent under review from publishing or editing its own evidence."""

    def run_hook(self, tool: str, tool_input: dict, allow_policy_edits: bool = False) -> int:
        root = tempfile.mkdtemp(prefix="kit-test-hook-")
        try:
            event = {"tool_name": tool, "tool_input": tool_input, "cwd": root}
            env = {k: v for k, v in __import__("os").environ.items() if k != "QUALITY_ALLOW_POLICY_EDITS"}
            env["CLAUDE_PROJECT_DIR"] = root
            if allow_policy_edits:
                env["QUALITY_ALLOW_POLICY_EDITS"] = "1"
            return subprocess.run([sys.executable, str(KIT / "hooks" / "protect_paths.py")], input=json.dumps(event),
                                  capture_output=True, text=True, env=env).returncode
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_blocks_publishing_and_forging(self):
        for cmd in ["python3 code_quality/scripts/evidence_comment.py publish --pr 4",
                    "gh pr comment 4 --body '<!-- quality-evidence sha=abc -->'",
                    "echo '{}' > .quality/review-0916ae2d8f65.json",
                    "jq '.blocking=false' .quality/risk.json | sponge",
                    "sed -i '' s/true/false/ .quality/review-abc.json"]:
            self.assertEqual(self.run_hook("Bash", {"command": cmd}), 2, cmd)
        self.assertEqual(self.run_hook("Write", {"file_path": "body.md", "content": "<!-- quality-evidence sha=a -->"}), 2)

    def test_blocks_editing_evidence_files_even_with_policy_edits_allowed(self):
        root_rel = ".quality/review-0916ae2d8f65.json"
        self.assertEqual(self.run_hook("Write", {"file_path": root_rel, "content": "{}"}, allow_policy_edits=True), 2)
        self.assertEqual(self.run_hook("Edit", {"file_path": ".quality/packet.md", "old_string": "a", "new_string": "b"},
                                       allow_policy_edits=True), 2)

    def test_mcp_and_multiedit_cannot_carry_the_marker_or_approve(self):
        marker = "<!-- quality-evidence sha=" + "a" * 40 + " -->"
        self.assertEqual(self.run_hook("mcp__github__add_issue_comment", {"owner": "o", "repo": "r", "body": marker}), 2)
        self.assertEqual(self.run_hook("MultiEdit", {"file_path": "x.md", "edits": [{"old_string": "a", "new_string": marker}]}), 2)
        self.assertEqual(self.run_hook("mcp__github__merge_pull_request", {"pullNumber": 4}), 2)
        self.assertEqual(self.run_hook("mcp__github__create_pull_request_review", {"event": "COMMENT", "body": "ok"}), 2)
        self.assertEqual(self.run_hook("mcp__github__add_issue_comment", {"body": "looks fine to me"}), 0)

    def test_settings_matcher_covers_mcp_tools(self):
        kit = json.loads((KIT / "hooks" / "settings.json").read_text())
        matchers = [e["matcher"] for e in kit["hooks"]["PreToolUse"]]
        self.assertTrue(any("mcp__" in m for m in matchers), matchers)

    def test_marker_guard_is_narrow(self):
        self.assertEqual(self.run_hook("Bash", {"command": "grep -rn quality-evidence ."}), 0)
        self.assertEqual(self.run_hook("Edit", {"file_path": ".github/workflows/quality-gate.yml",
                                                "old_string": "name: quality-evidence", "new_string": "name: quality-evidence"}), 0)
        marker = 'MARKER = "<!-- quality-evidence sha={sha} -->"'
        kit_edit = {"file_path": "code_quality/scripts/evidence_comment.py", "old_string": "x", "new_string": marker}
        self.assertEqual(self.run_hook("Edit", kit_edit, allow_policy_edits=True), 0)   # human-approved kit edit
        self.assertEqual(self.run_hook("Edit", kit_edit), 2)                            # otherwise blocked
        self.assertEqual(self.run_hook("Write", {"file_path": "notes.md", "content": marker}, allow_policy_edits=True), 2)

    def test_agents_cannot_post_statuses_or_check_runs(self):
        for cmd in ["gh api -X POST repos/o/r/statuses/abc -f state=success -f context=x",
                    "gh api repos/o/r/check-runs -f name=quality-gate -f conclusion=success",
                    "curl -X POST https://api.github.com/repos/o/r/statuses/abc"]:
            self.assertEqual(self.run_hook("Bash", {"command": cmd}), 2, cmd)
        self.assertEqual(self.run_hook("mcp__github__create_commit_status", {"state": "success"}), 2)
        self.assertEqual(self.run_hook("mcp__github__get_pull_request_status", {"pullNumber": 4}), 0)
        self.assertEqual(self.run_hook("mcp__claude_ai_Gamma__get_generation_status", {"id": "x"}), 0)
        self.assertEqual(self.run_hook("mcp__github__create_pull_request_review", {"event": "APPROVE"}), 2)

    def test_mcp_review_and_merge_tools_fail_closed_whatever_the_verb_position(self):
        # The GitHub MCP server's real tool: verb as a suffix, the approval in an argument.
        approve = {"method": "create", "event": "APPROVE", "body": "LGTM", "pullNumber": 4}
        self.assertEqual(self.run_hook("mcp__github__pull_request_review_write", approve), 2)
        self.assertEqual(self.run_hook("mcp__plugin_github_github__pull_request_review_write", approve), 2)
        for tool in ["mcp__github__submit_pending_pull_request_review", "mcp__github__pull_request_review_write",
                     "mcp__github__check_run_create", "mcp__github__commit_status_write", "mcp__x__review"]:
            self.assertEqual(self.run_hook(tool, {"body": "ok"}), 2, tool)
        # Verb in an argument, on a tool whose name says nothing about reviews or merges.
        self.assertEqual(self.run_hook("mcp__x__pull_request_write", {"method": "merge", "pullNumber": 4}), 2)
        self.assertEqual(self.run_hook("mcp__x__issue_write", {"method": "update", "state": "approved"}), 2)
        # Reading stays allowed, and so does the word in prose.
        self.assertEqual(self.run_hook("mcp__github__pull_request_read", {"method": "get_reviews", "pullNumber": 4}), 0)
        self.assertEqual(self.run_hook("mcp__github__list_pull_request_reviews", {"pullNumber": 4}), 0)
        self.assertEqual(self.run_hook("mcp__github__add_issue_comment", {"body": "please approve once CI is green"}), 0)

    def test_bash_approvals_in_every_spelling_are_blocked(self):
        for cmd in ['gh api -X POST "repos/o/r/pulls/$PR/reviews" -f event=APPROVE',
                    "gh api -X PUT repos/o/r/pulls/4/merge",
                    "gh api graphql -f query='mutation { addPullRequestReview(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
                    "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
                    "curl -d '{\"event\": \"APPROVE\"}' https://api.github.com/repos/o/r/pulls/4/reviews"]:
            self.assertEqual(self.run_hook("Bash", {"command": cmd}), 2, cmd)
        for cmd in ["gh pr view 4 --json reviews", "git merge --ff-only origin/main"]:
            self.assertEqual(self.run_hook("Bash", {"command": cmd}), 0, cmd)

    def test_mcp_sensitive_actions_only_allow_read_prefixes(self):
        for action in ["review_read", "merge_get", "approve_list", "status_search", "check_run_read",
                       "checkrun_get", "fetch_reviews", "find_reviews", "view_status", "show_reviews",
                       "describe_check_run", "download_reviews", "pull_request_review_write"]:
            with self.subTest(action=action):
                self.assertEqual(self.run_hook(f"mcp__plugin_github_github__{action}", {}), 2)
        for action in ["get_pull_request_status", "list_reviews", "search_check_runs", "read_review"]:
            with self.subTest(action=action):
                self.assertEqual(self.run_hook(f"mcp__github__{action}", {}), 0)

    def test_mcp_approve_event_overrides_even_read_tools(self):
        for tool in ["mcp__x__perform", "mcp__github__get_pull_request_status"]:
            for value in ["APPROVE", "approve", "ApPrOvE"]:
                for args in [{"event": value}, {"input": [{"event": value}]}]:
                    with self.subTest(tool=tool, args=args):
                        self.assertEqual(self.run_hook(tool, args), 2)

    def test_reading_and_running_the_loop_stay_allowed(self):
        for cmd in ["cat .quality/packet.md", "code_quality/scripts/review_loop.sh --intent x > .quality/loop.out 2>&1",
                    "python3 code_quality/scripts/evidence_comment.py fetch --pr 4 --sha abc", "swift test"]:
            self.assertEqual(self.run_hook("Bash", {"command": cmd}), 0, cmd)
        self.assertEqual(self.run_hook("Write", {"file_path": "Sources/a.swift", "content": "let x = 1"}), 0)


class WorkflowTrustedKit(unittest.TestCase):
    def test_every_staging_step_uses_the_base_copy_of_the_script(self):
        wf = (KIT / "ci" / "quality-gate.yml").read_text()
        self.assertEqual(wf.count('eval "$TRUSTED_KIT_FN"; trusted_kit'), 4)
        self.assertNotIn('run: bash code_quality/scripts/trusted_kit.sh', wf)
        self.assertIn('git show "$BASE_REF:code_quality/scripts/trusted_kit.sh" | bash -s', wf)


class ApprovalDepth(unittest.TestCase):
    def test_tier2_needs_deep_evidence(self):
        import approval_gate
        self.assertIsNotNone(approval_gate.depth_problem(2, {"depth": "standard"}))
        self.assertIsNotNone(approval_gate.depth_problem(2, {}))
        self.assertIsNone(approval_gate.depth_problem(2, {"depth": "deep"}))
        self.assertIsNone(approval_gate.depth_problem(1, {"depth": "standard"}))


class PolicyBinding(unittest.TestCase):
    def test_gate_rejects_evidence_from_another_policy(self):
        import approval_gate
        d = Path(tempfile.mkdtemp(prefix="kit-test-policy-"))
        try:
            (d / "quality.toml").write_text("version = 1\n")
            (d / "REVIEW.md").write_text("# base rules\n")
            good = qlib_policy(d / "quality.toml", d / "REVIEW.md")
            self.assertIsNone(approval_gate.policy_problem({"policy": good}, str(d / "quality.toml")))
            (d / "pr.toml").write_text("version = 1\n# weaker\n")
            other = qlib_policy(d / "pr.toml", d / "REVIEW.md")
            self.assertIsNotNone(approval_gate.policy_problem({"policy": other}, str(d / "quality.toml")))
            self.assertIsNotNone(approval_gate.policy_problem({}, str(d / "quality.toml")))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_load_config_honours_the_base_policy_override(self):
        import os, qlib
        d = Path(tempfile.mkdtemp(prefix="kit-test-policy-"))
        try:
            (d / "base.toml").write_text('version = 1\n[review]\ndepth = "deep"\n')
            old = os.environ.get("QUALITY_POLICY")
            os.environ["QUALITY_POLICY"] = str(d / "base.toml")
            try:
                self.assertEqual(qlib.load_config()["review"]["depth"], "deep")
            finally:
                os.environ.pop("QUALITY_POLICY") if old is None else os.environ.__setitem__("QUALITY_POLICY", old)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_workflow_fails_bootstrap_prs_before_running_any_staged_script(self):
        wf = (KIT / "ci" / "quality-gate.yml").read_text()
        gate = wf[wf.index("\n  quality-gate:"):]
        staging = gate[gate.index('eval "$TRUSTED_KIT_FN"; trusted_kit'):]
        check = staging.index("if [ -f $RUNNER_TEMP/trusted/BOOTSTRAP ]")
        first_script = staging.index("$RUNNER_TEMP/trusted/code_quality/scripts/")
        self.assertLess(check, first_script, "the bootstrap check must come before any staged script runs")
        self.assertIn("exit 1", staging[check:first_script])
        # The policy job reads the marker itself, not through a staged (possibly PR-supplied) script.
        policy = wf[wf.index("\n  policy:"):wf.index("\n  checks:")]
        self.assertIn('if [ -f $RUNNER_TEMP/trusted/BOOTSTRAP ]; then echo "bootstrap=true"', policy)
        self.assertIn("grep -v '^bootstrap='", policy)


def qlib_policy(toml: Path, rules: Path) -> str:
    import qlib
    return qlib.policy_fingerprint(toml, rules)


class GateHardening(unittest.TestCase):
    def wf(self):
        return (KIT / "ci" / "quality-gate.yml").read_text()

    def job(self, name):
        wf = self.wf(); start = wf.index(f"\n  {name}:")
        nxt = re.search(r"\n  [a-z-]+:\n", wf[start + 3:])
        return wf[start: start + 3 + nxt.start()] if nxt else wf[start:]

    def step_script(self, name):
        step = self.job("quality-gate").split(f"      - name: {name}\n", 1)[1]
        step = step.split("\n      - ", 1)[0]
        return textwrap.dedent(step.split("        run: |\n", 1)[1])

    def enforce(self, root, mode, *, checks="success", review="success", event="pull_request",
                gate_rc=3, risk_rc=0, risk_output=None, env_overrides=None):
        values = {"needs.policy.outputs.review_where": mode, "needs.checks.result": checks,
                  "needs.review.result": review, "github.event_name": event,
                  "github.event.pull_request.number": "4", "github.event.pull_request.head.sha": "a" * 40}
        script = re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: values[m[1]], self.step_script("Enforce"))
        # Stub only the external verdicts; execute the real shell and inline JSON parser.
        stub = r'''
        python3() {
          case "$1" in
            */classify_risk.py)
              printf '%s\n' "$@" > "$RISK_CALLS"
              printf '%s\n' "$RISK_OUTPUT"
              return "$RISK_RC" ;;
            */approval_gate.py)
              printf '%s\n' "$@" > "$GATE_CALLS"
              return "$GATE_RC" ;;
            *) command python3 "$@" ;;
          esac
        }
        '''
        (root / "summary").write_text("")
        env = {**os.environ, "RUNNER_TEMP": str(root / "runner"), "BASE_REF": "base",
               "EVIDENCE_DIR": str(root / "runner" / "evidence"),
               "GITHUB_STEP_SUMMARY": str(root / "summary"),
               "GATE_CALLS": str(root / "calls"), "GATE_RC": str(gate_rc),
               "RISK_CALLS": str(root / "risk-calls"), "RISK_RC": str(risk_rc),
               "RISK_OUTPUT": risk_output if risk_output is not None else
               json.dumps({"tier": 2, "approvals_required": 2})}
        env.update(env_overrides or {})
        env = {key: value for key, value in env.items() if value is not None}
        return subprocess.run(["bash", "-e", "-c", stub + script], cwd=root / "checkout",
                              env=env, capture_output=True, text=True)

    def test_enforce_reports_all_problems_in_order_without_early_exit(self):
        with tempfile.TemporaryDirectory(prefix="kit-test-enforce-") as tmp:
            root = Path(tmp)
            (root / "checkout").mkdir()
            evidence = root / "runner" / "evidence"
            evidence.mkdir(parents=True)
            for present in (False, True):
                with self.subTest(evidence=present):
                    if present:
                        (evidence / "review-head.json").write_text("{}")
                    result = self.enforce(root, "ci", checks="failure", review="failure", gate_rc=1)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    rows = (root / "summary").read_text().splitlines()
                    self.assertIn("❌ Checks failed", rows[0])
                    self.assertIn("❌ Review job failed", rows[1])
                    self.assertIn("ℹ️ Risk: Tier 2; trusted policy requires 2 human approval(s).", rows[2])
                    if present:
                        self.assertIn("✅ Review evidence", rows[3])
                        self.assertIn("❌ Review blocked: review blocked: see the log above", rows[4])
                        self.assertTrue((root / "calls").exists())
                    else:
                        self.assertIn("⏳ Waiting for you", rows[3])
                        self.assertIn("Wait for the CI review run to finish, then re-run this job.", rows[3])
                        self.assertFalse((root / "calls").exists())
                    self.assertEqual(len(rows), 5 if present else 4)
                    self.assertTrue(all(row.startswith(("- [x] ", "- [ ] ")) for row in rows))
                    annotations = [line for line in result.stdout.splitlines() if line.startswith("::error")]
                    self.assertEqual(len(annotations), 3)
                    self.assertIn("::error title=❌ Checks failed::", annotations[0])
                    self.assertIn("::error title=❌ Review job failed::", annotations[1])
                    self.assertTrue(annotations[2].startswith(
                        "::error title=❌" if present else
                        "::error title=⏳ Waiting for you (not a failure in the code)::"))
                    self.assertEqual((root / "risk-calls").read_text().splitlines(), [
                        str(root / "runner/trusted/code_quality/scripts/classify_risk.py"),
                        "--base", "base", "--config", str(root / "runner/trusted/quality.toml")])

    def test_enforce_risk_errors_do_not_hide_evidence_or_verdict(self):
        with tempfile.TemporaryDirectory(prefix="kit-test-enforce-") as tmp:
            root = Path(tmp)
            (root / "checkout").mkdir()
            evidence = root / "runner/evidence"
            evidence.mkdir(parents=True)
            for present in (False, True):
                if present:
                    (evidence / "review-head.json").write_text("{}")
                for risk_rc, output in [(1, None), (127, None), (0, "not json"), (0, "{}"),
                                        (0, '{"tier": 9, "approvals_required": 0}')]:
                    with self.subTest(present=present, rc=risk_rc, output=output):
                        result = self.enforce(root, "local", risk_rc=risk_rc, risk_output=output, gate_rc=0)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        summary = (root / "summary").read_text()
                        self.assertIn("❌ Risk classification failed", summary)
                        self.assertIn("::error title=❌ Risk classification failed::", result.stdout)
                        self.assertIn("✅ Review approved" if present else "⏳ Waiting for you", summary)
                        if not present:
                            self.assertIn("Review it locally, then Run:", summary)
                            self.assertIn("evidence_comment.py publish --pr 4 --approve", summary)

    def test_enforce_approval_exit_codes_and_applicable_review_job(self):
        with tempfile.TemporaryDirectory(prefix="kit-test-enforce-") as tmp:
            root = Path(tmp)
            (root / "checkout").mkdir()
            evidence = root / "runner/evidence"
            evidence.mkdir(parents=True)
            (evidence / "review-head.json").write_text("{}")
            for mode in ("ci", "local"):
                for rc in (0, 3, 1, 2, 127):
                    with self.subTest(mode=mode, rc=rc):
                        review_applies = mode == "ci"
                        result = self.enforce(root, mode, gate_rc=rc,
                                              review="success" if review_applies else "skipped")
                        self.assertEqual(result.returncode, 0 if rc == 0 else 1, result.stderr)
                        summary = (root / "summary").read_text()
                        self.assertIn("✅ Deterministic checks", summary)
                        self.assertEqual("✅ CI review job" in summary, review_applies)
                        expected = {0: "✅ Review approved", 3: "⏳ Waiting for your sign-off"}.get(
                            rc, "❌ Review blocked")
                        self.assertIn(expected, summary)
                        self.assertEqual(result.stdout.count("::error"), 0 if rc == 0 else 1)
                        args = (root / "calls").read_text().splitlines()
                        self.assertEqual(args, [str(root / "runner/trusted/code_quality/scripts/approval_gate.py"),
                                               "--pr", "4", "--base", "base", "--config",
                                               str(root / "runner/trusted/quality.toml"),
                                               "--risk", str(evidence / "risk.json"),
                                               "--review", str(evidence / "review-head.json")])

    def test_enforce_fails_closed_on_missing_environment_or_summary_write_error(self):
        with tempfile.TemporaryDirectory(prefix="kit-test-enforce-") as tmp:
            root = Path(tmp)
            (root / "checkout").mkdir()
            evidence = root / "runner/evidence"
            evidence.mkdir(parents=True)
            (evidence / "review-head.json").write_text("{}")
            for value in (None, ""):
                with self.subTest(evidence_dir=value):
                    result = self.enforce(root, "ci", gate_rc=0, env_overrides={"EVIDENCE_DIR": value})
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn("❌ Missing evidence directory", (root / "summary").read_text())
                    self.assertIn("::error title=❌ Missing evidence directory::", result.stdout)
                    self.assertFalse((root / "calls").exists())
            for value in (None, str(root)):
                with self.subTest(summary=value):
                    result = self.enforce(root, "ci", gate_rc=0, env_overrides={"GITHUB_STEP_SUMMARY": value})
                    self.assertNotEqual(result.returncode, 0)

    def test_evidence_acquisition_uses_a_fresh_runner_temp_directory(self):
        gate = self.job("quality-gate")
        self.assertIn('path: "${{ env.EVIDENCE_DIR }}"', gate)
        self.assertIn('--out "$EVIDENCE_DIR"', gate)
        with tempfile.TemporaryDirectory(prefix="kit-test-evidence-") as tmp:
            root = Path(tmp)
            env = {**os.environ, "RUNNER_TEMP": tmp, "GITHUB_ENV": str(root / "env")}
            paths = []
            for _ in range(2):
                subprocess.run(["bash", "-e", "-c", self.step_script("Fresh evidence directory")],
                               cwd=root, env=env, check=True, capture_output=True, text=True)
                path = Path((root / "env").read_text().splitlines()[-1].removeprefix("EVIDENCE_DIR="))
                self.assertEqual(path.parent, root)
                self.assertTrue(path.is_dir())
                self.assertEqual(list(path.iterdir()), [])
                paths.append(path)
            self.assertNotEqual(*paths)

    def test_gate_removes_planted_quality_directory_and_symlinks_before_classification(self):
        gate = self.job("quality-gate")
        self.assertLess(gate.index("rm -rf .quality"), gate.index("/scripts/classify_risk.py"))
        with tempfile.TemporaryDirectory(prefix="kit-test-clean-quality-") as tmp:
            root = Path(tmp)
            checkout = root / "checkout"
            checkout.mkdir()
            trusted = root / "trusted"
            trusted.mkdir()
            victim = trusted / "risk.json"
            victim.write_text("trusted script")
            planted = checkout / ".quality"
            for kind in ("directory", "directory_symlink", "file_symlink"):
                with self.subTest(kind=kind):
                    if kind == "directory_symlink":
                        planted.symlink_to(trusted, target_is_directory=True)
                    else:
                        planted.mkdir()
                        if kind == "file_symlink":
                            (planted / "risk.json").symlink_to(victim)
                        else:
                            (planted / "risk.json").write_text("planted evidence")
                    subprocess.run(["bash", "-e", "-c", self.step_script("Fresh evidence directory")],
                                   cwd=checkout, env={**os.environ, "RUNNER_TEMP": tmp,
                                                      "GITHUB_ENV": str(root / "env")},
                                   check=True, capture_output=True, text=True)
                    self.assertFalse(planted.exists())
                    self.assertFalse(planted.is_symlink())
                    self.assertEqual(victim.read_text(), "trusted script")

    def test_enforce_ignores_planted_workspace_evidence(self):
        with tempfile.TemporaryDirectory(prefix="kit-test-evidence-") as tmp:
            root = Path(tmp)
            planted = root / "checkout" / ".quality"
            planted.mkdir(parents=True)
            evidence = root / "runner" / "evidence"
            evidence.mkdir(parents=True)
            for name in ["review-head.json", "risk.json"]:
                (planted / name).write_text('{"blocking": false}')
            for mode in ["local", "ci"]:
                with self.subTest(mode=mode):
                    result = self.enforce(root, mode)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("No review evidence", result.stdout)
                    self.assertFalse((root / "calls").exists(), "planted evidence must never reach the gate")
            for name in ["review-head.json", "risk.json"]:
                (evidence / name).write_text("{}")
            for mode in ["local", "ci"]:
                self.enforce(root, mode)
                args = (root / "calls").read_text().splitlines()
                self.assertEqual(args[args.index("--risk") + 1], str(evidence / "risk.json"))
                self.assertEqual(args[args.index("--review") + 1], str(evidence / "review-head.json"))

    def test_waiting_annotation_matches_review_mode(self):
        with tempfile.TemporaryDirectory(prefix="kit-test-wait-") as tmp:
            root = Path(tmp)
            (root / "checkout").mkdir()
            # Include the old location too, so this catches the message bug independently.
            for evidence in [root / "runner" / "evidence", root / "checkout" / ".quality"]:
                evidence.mkdir(parents=True)
                (evidence / "review-head.json").write_text("{}")
            local = self.enforce(root, "local")
            ci = self.enforce(root, "ci")
            self.assertEqual(local.returncode, 1)
            self.assertEqual(ci.returncode, 1)
            self.assertIn("evidence_comment.py publish --pr 4 --approve", local.stdout)
            self.assertIn("Waiting for your sign-off", ci.stdout)
            self.assertNotIn("publish", ci.stdout)
            self.assertIn("Approve", ci.stdout)
            self.assertIn("head commit", ci.stdout)
            self.assertIn("/director-approve", ci.stdout)

    def test_no_job_writes_statuses_and_only_review_writes_prs(self):
        wf = self.wf()
        self.assertNotIn("statuses", wf)
        top = wf[wf.index("\npermissions:"):wf.index("\nconcurrency:")]
        self.assertNotIn("write", top)
        self.assertIn("pull-requests: write", self.job("review"))
        for name in ("policy", "checks", "quality-gate"):
            self.assertNotIn("pull-requests: write", self.job(name), name)

    def test_every_workflow_declares_top_level_permissions_without_write(self):
        for path in sorted((KIT / "ci").glob("*.yml")):
            with self.subTest(workflow=path.name):
                permissions = []
                in_permissions = False
                for line in path.read_text().splitlines():
                    line = line.split("#", 1)[0].rstrip()
                    if not line.strip():
                        continue
                    if not line[0].isspace():
                        in_permissions = line.startswith("permissions:")
                    if in_permissions:
                        permissions.append(line)
                self.assertTrue(permissions, "workflow must declare top-level permissions")
                self.assertNotRegex("\n".join(permissions), r"\bwrite\b",
                                    "write permissions must be scoped to jobs")

    def test_gate_runs_only_on_pr_and_merge_group_with_one_group_per_pr(self):
        wf = self.wf()
        triggers = wf.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"(?m)^  (\w+):", triggers), ["pull_request", "merge_group"])
        self.assertIn("types: [opened, synchronize, reopened, ready_for_review, labeled]", triggers)
        self.assertNotIn("pull_request_review", wf)
        self.assertNotIn("Evidence from this head's latest review run", wf)
        concurrency = wf.split("\nconcurrency:\n", 1)[1].split("\nenv:", 1)[0]
        self.assertEqual(concurrency.strip(),
                         "group: quality-gate-${{ github.event.pull_request.number || github.ref }}\n"
                         "  cancel-in-progress: true")

    def test_gate_fails_when_pr_checks_did_not_pass(self):
        with tempfile.TemporaryDirectory(prefix="kit-test-enforce-") as tmp:
            root = Path(tmp)
            (root / "checkout").mkdir()
            evidence = root / "runner/evidence"
            evidence.mkdir(parents=True)
            (evidence / "review-head.json").write_text("{}")
            for checks in ("failure", "cancelled", "skipped"):
                with self.subTest(checks=checks):
                    result = self.enforce(root, "ci", checks=checks, gate_rc=0)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    summary = (root / "summary").read_text()
                    self.assertIn("❌ Checks failed", summary)
                    self.assertIn("✅ Review approved", summary)

    def test_the_job_is_the_verdict_and_waiting_says_so(self):
        gate = self.job("quality-gate")
        self.assertNotIn("GATE_STATE", gate)
        self.assertIn("Waiting for you (not a failure in the code)", gate)
        self.assertIn("Waiting for your sign-off (not a failure in the code)", gate)
        self.assertIn("--approve", gate)

    def test_kit_is_staged_outside_the_checkout(self):
        wf = self.wf()
        self.assertNotIn("_trusted", wf)
        self.assertIn('"$BASE_REF" $RUNNER_TEMP/trusted', wf)

    def test_no_shell_variable_inside_single_quoted_python(self):
        for line in self.wf().splitlines():
            for m in re.finditer(r"python3[^']*'([^']*)'", line):
                self.assertNotIn("$", m.group(1), line)

    def test_inline_python_is_isolated(self):
        # Inline Python runs with the PR checkout as cwd; without -I a PR-planted tomllib.py or json.py
        # would be imported instead of the standard library.
        for path in [*(KIT / "ci").glob("*.yml"), *(KIT / "scripts").glob("*.sh")]:
            for line in path.read_text().splitlines():
                if re.search(r"python3 (-c|-)( |$)", line):
                    self.fail(f"{path.name}: inline python without -I: {line.strip()}")

    def test_staging_refuses_a_prefilled_target(self):
        with TempRepo() as r:
            r.write("code_quality/scripts/qlib.py", "# base\n")
            r.write("code_quality/quality.toml", "version = 1\n")
            r.commit("base")
            base = git(r.path, "rev-parse", "HEAD").strip()
            r.write("_trusted/code_quality/scripts/json.py", "raise SystemExit('planted')\n")
            r.commit("pr plants a module where the kit is staged")
            res = subprocess.run(["bash", str(KIT / "scripts" / "trusted_kit.sh"), base, "_trusted"], cwd=r.path,
                                 capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("not empty", res.stderr)

    def test_actions_pinned_and_no_persisted_credentials(self):
        for path in (KIT / "ci").glob("*.yml"):
            wf = path.read_text()
            for line in re.findall(r"(?m)^\s*(?:-\s*)?uses: (\S+)", wf):
                self.assertRegex(line, r"@[0-9a-f]{40}$", line)
            self.assertEqual(wf.count("actions/checkout@"), wf.count("persist-credentials: false"))

    def test_signoff_is_pinned_to_the_evidenced_commit(self):
        src = (KIT / "scripts" / "evidence_comment.py").read_text()
        self.assertIn('f"commit_id={sha}"', src)
        self.assertNotIn('"gh", "pr", "review"', src)

    def test_loop_never_reviews_an_empty_delta(self):
        self.assertIn('[ "$last_reviewed" != "$(git rev-parse HEAD)" ]', (KIT / "scripts" / "review_loop.sh").read_text())


class GateRefreshWorkflow(unittest.TestCase):
    SHA = "a" * 40
    TITLE = f"quality-gate review event for PR #7 at {'a' * 40}"
    GH_STUB = r"""
        gh() {
          printf '%s\n' "$*" >> "$CALLS"
          case " $* " in *" --slurp "*" --jq "*|*" --jq "*" --slurp "*)
            echo "the \`--slurp\` option is not supported with \`--jq\` or \`--template\`" >&2; return 1 ;; esac
          next() { local f="$1" n; read -r n < "$f.i"; printf '%s\n' "$((n + 1))" > "$f.i"; sed -n "$((n + 1))p" "$f"; }
          case "$*" in
            "api repos/o/r/pulls/7") jq -n --arg sha "$(next "$PR_HEADS")" --arg repo "$HEAD_REPO" --arg ref "$HEAD_REF" \
                '{head: {sha: $sha, ref: $ref, repo: (if $repo == "" then null else {full_name: $repo} end)}}' ;;
            "api repos/o/r/pulls/7 --jq .head.sha") next "$PR_HEADS" ;;
            *actions/workflows/quality-gate.yml/runs*) cat "$RUNS" ;;
            "api repos/o/r/actions/runs/"*"/jobs"*) printf '%s\n' "$JOB" ;;
            "api repos/o/r/actions/runs/"*) next "$STATES" ;;
            "run rerun "*) return "$RERUN_RC" ;;
            *) echo "unexpected gh call: $*" >&2; return 1 ;;
          esac
        }
        sleep() { printf '%s\n' "$1" >> "$SLEEPS"; SECONDS=$((SECONDS + $1)); }
    """

    def wf(self):
        return (KIT / "ci/quality-gate-refresh.yml").read_text()

    def script(self):
        return textwrap.dedent(self.wf().split("        run: |\n", 1)[1])

    def test_relay_holds_no_permissions_and_names_pr_and_head_in_its_title(self):
        relay = (KIT / "ci/quality-gate-review.yml").read_text()
        triggers = relay.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(triggers.strip(), "pull_request_review:\n    types: [submitted, dismissed]")
        self.assertIn("\npermissions: {}\n", relay)
        self.assertEqual(relay.count("permissions:"), 1)
        self.assertIn("\nname: quality-gate-review\n", relay)
        # Must stay quoted: in a plain YAML scalar " #" starts a comment, which truncated the title live.
        self.assertIn('run-name: "quality-gate review event for PR #${{ github.event.pull_request.number }} '
                      'at ${{ github.event.pull_request.head.sha }}"\n', relay)
        self.assertNotIn("uses:", relay)
        self.assertNotIn("secrets.", relay)

    def test_refresh_runs_from_default_branch_with_minimal_permissions_without_repository_code(self):
        wf = self.wf()
        triggers = wf.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(triggers.strip(), "workflow_run:\n    workflows: [quality-gate-review]\n    types: [completed]")
        self.assertIn("\npermissions: {}\n", wf)
        refresh = wf.split("\njobs:\n  refresh:\n", 1)[1]
        permissions = refresh.split("\n    permissions:\n", 1)[1].split("\n    env:", 1)[0]
        entries = [line.split("#", 1)[0].strip() for line in permissions.splitlines()]
        self.assertEqual(len([line for line in entries if line]), 3)
        self.assertEqual(dict(line.split(": ", 1) for line in entries if line),
                         {"contents": "read", "actions": "write", "pull-requests": "read"})
        self.assertEqual(wf.count("permissions:"), 2)
        self.assertIn("if: github.event.workflow_run.event == 'pull_request_review'", wf)
        self.assertIn("TITLE: ${{ github.event.workflow_run.display_title }}", wf)
        # Per PR and head: a refresh for another PR sharing the commit must not cancel this one.
        self.assertIn("group: quality-gate-refresh-${{ github.event.workflow_run.display_title }}", wf)
        self.assertNotIn("actions/checkout", wf)
        self.assertNotIn("uses:", wf)
        self.assertNotIn("python", wf.lower())
        self.assertNotIn("code_quality/", self.script())
        self.assertNotIn("${{", self.script())   # untrusted values reach the shell only through env
        self.assertIn('gh run rerun "$RUN" --repo "$GH_REPO" --job "$JOB"', self.script())
        self.assertIn("max_by([.created_at, .id])", self.script())
        self.assertIn('select(.name == "quality-gate")', self.script())

    def test_gate_run_name_is_quoted_and_identifies_the_pr(self):
        gate = (KIT / "ci/quality-gate.yml").read_text()
        line = next(line for line in gate.splitlines() if line.startswith("run-name:"))
        self.assertRegex(line, r'^run-name: ".*quality-gate for PR #.*"$')
        self.assertIn("github.event_name == 'pull_request'", line)
        self.assertIn("format('quality-gate for PR #{0}', github.event.pull_request.number)", line)
        self.assertIn("|| github.workflow", line)

    def installed(self):
        names = [n for n in re.findall(r'"(quality-gate[\w-]*\.yml)"', (KIT / "install.py").read_text())]
        return names

    def test_install_ships_gate_relay_and_refresh(self):
        self.assertEqual(sorted(set(self.installed())),
                         ["quality-gate-refresh.yml", "quality-gate-review.yml", "quality-gate.yml"])

    def run_obj(self, id, *, pr=7, repo="o/r", branch="feature", created=None, event="pull_request", title=None):
        return {"id": id, "event": event, "head_sha": self.SHA, "created_at": created or f"2026-11-15T10:00:{id:02d}Z",
                "pull_requests": [{"number": pr}] if pr else [], "head_repository": {"full_name": repo},
                "head_branch": branch, "display_title": title}

    def execute(self, *, title=None, runs=None, pr_heads=None, states=None, job="21",
                head_repo="o/r", head_ref="feature", rerun_rc=0):
        """Run the real workflow shell. gh is a stub that serves JSON from files and rejects flag
        combinations the real CLI rejects; jq and bash are real."""
        runs = runs if runs is not None else [self.run_obj(2)]
        pr_heads = list(pr_heads or [self.SHA, self.SHA])
        states = list(states or ["completed success"])
        with tempfile.TemporaryDirectory(prefix="kit-test-refresh-") as tmp:
            root = Path(tmp)
            for name, lines in (("pr_heads", pr_heads), ("states", states)):
                (root / name).write_text("".join(f"{line}\n" for line in lines))
                (root / f"{name}.i").write_text("0\n")
            (root / "runs").write_text(json.dumps({"workflow_runs": runs}))
            (root / "sleeps").touch()
            (root / "calls").touch()
            env = {**os.environ, "GH_REPO": "o/r", "TITLE": title if title is not None else self.TITLE,
                   "JOB": job, "HEAD_REPO": head_repo, "HEAD_REF": head_ref, "RUNS": str(root / "runs"),
                   "PR_HEADS": str(root / "pr_heads"), "STATES": str(root / "states"),
                   "CALLS": str(root / "calls"), "SLEEPS": str(root / "sleeps"), "RERUN_RC": str(rerun_rc)}
            result = subprocess.run(["bash", "-e", "-c", self.GH_STUB + self.script()], cwd=root,
                                    env=env, capture_output=True, text=True)
            return result, (root / "calls").read_text().splitlines(), (root / "sleeps").read_text().splitlines()

    def reruns(self, calls):
        return [c for c in calls if c.startswith("run rerun")]

    def test_waits_for_completion_then_reruns_only_this_prs_gate_job(self):
        result, calls, sleeps = self.execute(states=["queued ", "in_progress ", "completed failure"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sleeps, ["15", "15"])
        self.assertEqual(self.reruns(calls), ["run rerun 2 --repo o/r --job 21"])
        self.assertIn("api repos/o/r/pulls/7 --jq .head.sha", calls)   # live head re-checked before re-running
        live = calls.index("api repos/o/r/pulls/7 --jq .head.sha")
        self.assertEqual(calls[live + 1], "run rerun 2 --repo o/r --job 21")   # nothing between check and re-run
        self.assertIn("/jobs?", calls[live - 1])

    def test_gh_stub_rejects_slurp_with_jq_like_the_real_cli(self):
        # Every behavioral test above runs the workflow through this stub, so a --slurp/--jq call fails them.
        for args in ("--paginate --slurp u --jq .", "--jq . --slurp u"):
            with self.subTest(args=args):
                res = subprocess.run(["bash", "-c", self.GH_STUB + f"gh api {args}"],
                                     env={**os.environ, "CALLS": os.devnull}, capture_output=True, text=True)
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("not supported with `--jq`", res.stderr)

    def test_malformed_relay_title_fails_without_any_api_call(self):
        for title in ("", "quality-gate review event for PR #7 at abc", f"quality-gate review event for PR #7; rm -rf / at {self.SHA}",
                      f"quality-gate review event for PR #7 at {self.SHA}\nmore",
                      f"quality-gate review event for PR #7 at {self.SHA}\n",
                      f"quality-gate review event for PR #0 at {self.SHA}",
                      f"quality-gate review event for PR #07 at {self.SHA}"):
            with self.subTest(title=title):
                result, calls, _ = self.execute(title=title)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("::error title=Unrecognised relay run::", result.stdout)
                self.assertEqual(calls, [])

    def test_title_naming_a_commit_that_is_not_the_live_head_reruns_nothing(self):
        result, calls, _ = self.execute(pr_heads=["b" * 40])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice title=PR head moved::", result.stdout)
        self.assertEqual(calls, ["api repos/o/r/pulls/7"])

    def test_selects_only_runs_of_this_pr_newest_first(self):
        runs = [self.run_obj(2), self.run_obj(5, pr=8, branch="other"),       # another PR sharing the commit
                self.run_obj(4, event="pull_request_review"), self.run_obj(3),
                {**self.run_obj(6), "head_sha": "b" * 40}]
        result, calls, _ = self.execute(runs=runs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.reruns(calls), ["run rerun 3 --repo o/r --job 21"])

    def test_fork_runs_sharing_a_head_match_only_the_exact_pr_title(self):
        fork = self.run_obj(3, pr=None, repo="someone/fork", title="quality-gate for PR #7")
        decoy = self.run_obj(4, pr=None, repo="someone/fork", title="quality-gate for PR #8")
        result, calls, _ = self.execute(runs=[fork, decoy], head_repo="someone/fork")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.reruns(calls), ["run rerun 3 --repo o/r --job 21"])
        result, calls, _ = self.execute(runs=[fork], head_repo="")   # deleted fork: no match, no re-run
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice title=No PR gate run yet::", result.stdout)
        self.assertEqual(self.reruns(calls), [])

    def test_fork_run_with_missing_or_inexact_title_is_not_selected(self):
        for title in (None, "The PR title", "quality-gate for PR #8", "quality-gate for PR #70",
                      "quality-gate for PR #7 extra", "quality-gate for PR #7\n"):
            with self.subTest(title=title):
                run = self.run_obj(3, pr=None, repo="someone/fork", title=title)
                result, calls, _ = self.execute(runs=[run], head_repo="someone/fork")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("::notice title=No PR gate run yet::", result.stdout)
                self.assertEqual(self.reruns(calls), [])

    def test_fork_title_cannot_override_head_repository_branch_or_pr_list(self):
        for overrides in ({"repo": "other/fork"}, {"branch": "elsewhere"},
                          {"repo": "other/fork", "branch": "elsewhere"}, {"pr": 8}):
            with self.subTest(overrides=overrides):
                fields = {"pr": None, "repo": "someone/fork", "title": "quality-gate for PR #7", **overrides}
                result, calls, _ = self.execute(runs=[self.run_obj(3, **fields)], head_repo="someone/fork")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("::notice title=No PR gate run yet::", result.stdout)
                self.assertEqual(self.reruns(calls), [])

    def test_refused_rerun_fails_with_actionable_annotation(self):
        result, calls, _ = self.execute(rerun_rc=1)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.reruns(calls), ["run rerun 2 --repo o/r --job 21"])
        self.assertIn("::error title=Gate re-run refused::", result.stdout)
        self.assertIn("quality-gate.yml run 2", result.stdout)
        self.assertIn("push a commit or close and reopen PR #7 to start a fresh gate run", result.stdout)
        self.assertNotIn("Re-running quality-gate job", result.stdout)

    def test_cancelled_run_is_superseded_and_not_rerun(self):
        result, calls, _ = self.execute(states=["completed cancelled"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::notice title=Gate run superseded::", result.stdout)
        self.assertEqual(self.reruns(calls), [])

    def test_head_moving_while_waiting_skips_the_stale_rerun(self):
        result, calls, _ = self.execute(pr_heads=[self.SHA, "b" * 40], states=["in_progress ", "completed success"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("moved past", result.stdout)
        self.assertEqual(self.reruns(calls), [])

    def test_no_run_emits_notice_and_exits_successfully(self):
        result, calls, sleeps = self.execute(runs=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("will read the review when it runs", result.stdout)
        self.assertEqual(sleeps, [])
        self.assertEqual(self.reruns(calls), [])

    def test_timeout_fails_with_annotation_without_rerunning(self):
        result, calls, sleeps = self.execute(states=["in_progress "] * 1000)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("::error title=Gate refresh timed out::", result.stdout)
        # bash's real SECONDS clock can tick during the run while the fake sleep returns at once.
        total = sum(map(int, sleeps))
        self.assertLessEqual(total, 1200)
        self.assertGreaterEqual(total, 1200 - 15)
        self.assertTrue(all(0 < int(seconds) <= 15 for seconds in sleeps))
        self.assertFalse(any("run rerun" in call or "/jobs?" in call for call in calls))

    def test_missing_job_fails_with_annotation(self):
        result, calls, _ = self.execute(job="")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("::error title=Missing gate job::", result.stdout)
        self.assertEqual(self.reruns(calls), [])


class GateLiveBase(unittest.TestCase):
    def gate_job(self):
        wf = (KIT / "ci/quality-gate.yml").read_text()
        return wf.split("\n  quality-gate:\n", 1)[1]

    def test_gate_job_resolves_the_live_base_before_staging_the_trusted_kit(self):
        job = self.gate_job()
        live = job.index("- name: Live PR base")
        staging = job.index("- name: Trusted kit from base branch")
        self.assertLess(live, staging)
        # The tip of the base branch: the pulls API's base.sha lags behind it (it kept a revoked
        # reviewer's policy in force in a live test).
        self.assertIn('gh api "repos/$GITHUB_REPOSITORY/pulls/$PR" --jq \'.base.ref\'', job)
        self.assertIn('gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/$BASE_PATH" --jq \'.object.sha\'', job)
        self.assertNotIn(".base.sha'", job)
        self.assertIn('echo "LIVE_BASE=$LIVE_BASE" >> "$GITHUB_ENV"', job)
        # Staging and Enforce (tier floor, approvals) use the live base, not the event's.
        self.assertEqual(job.count("BASE_REF: ${{ env.LIVE_BASE }}"), 2)
        self.assertIn("BASE_REF: ${{ env.LIVE_BASE }}", job[staging:job.index("- name: Fresh evidence directory")])
        self.assertIn("BASE_REF: ${{ env.LIVE_BASE }}", job[job.index("- name: Enforce"):])

    def test_gate_fails_closed_when_the_live_policy_moved_reviews(self):
        job = self.gate_job()
        staging = job[job.index("- name: Trusted kit from base branch"):job.index("- name: Fresh evidence directory")]
        self.assertIn("RUN_REVIEW_WHERE: ${{ needs.policy.outputs.review_where }}", staging)
        self.assertIn('if [ "$live_where" != "$RUN_REVIEW_WHERE" ]; then', staging)
        self.assertIn("::error title=Base policy changed::", staging)
        self.assertLess(staging.index("BOOTSTRAP"), staging.index("ci_policy.py"))   # no staged script before the bootstrap check

    def test_unavailable_or_malformed_live_base_fails_closed(self):
        job = self.gate_job()
        step = job[job.index("- name: Live PR base"):job.index("- name: Trusted kit from base branch")]
        self.assertIn("set -euo pipefail", step)
        self.assertIn('[[ ! "$LIVE_BASE" =~ ^[0-9a-f]{40}$ ]]', step)
        staging = job[job.index("- name: Trusted kit from base branch"):job.index("- name: Fresh evidence directory")]
        self.assertLess(staging.index('[[ "$BASE_REF" =~ ^[0-9a-f]{40}$ ]]'), staging.index("trusted_kit\n"))
        self.assertIn('git cat-file -e "$LIVE_BASE^{commit}"', step)
        self.assertEqual(step.count("exit 1"), 2)


class PublishReruns(unittest.TestCase):
    SHA = "a" * 40
    PR = {"number": 4, "head": {"sha": "a" * 40, "ref": "feature", "repo": {"full_name": "o/r"}}}
    FORK_PR = {"number": 7, "head": {"sha": "a" * 40, "ref": "feature", "repo": {"full_name": "x/fork"}}}

    def run_data(self, id=2, **overrides):
        return {"id": id, "event": "pull_request", "head_sha": self.SHA,
                "created_at": f"2026-11-15T10:00:{id:02d}Z", "status": "completed",
                "conclusion": "success", "pull_requests": [{"number": 4}],
                "head_repository": {"full_name": "o/r"}, "head_branch": "feature", **overrides}

    def test_selects_newest_pr_run_for_sha_without_mutating_input(self):
        newest = self.run_data(status="in_progress", conclusion=None)
        runs = [self.run_data(3, event="pull_request_review"), newest,
                self.run_data(4, event="merge_group"), self.run_data(5, event="push"),
                self.run_data(1, updated_at="2026-11-16T10:00:00Z", run_attempt=3),
                self.run_data(6, head_sha="b" * 40)]
        before = json.dumps(runs)
        self.assertIs(evidence_comment.select_gate_run(runs, self.SHA, self.PR), newest)
        self.assertEqual(json.dumps(runs), before)
        self.assertIsNone(evidence_comment.select_gate_run([], self.SHA, self.PR))
        self.assertIsNone(evidence_comment.select_gate_run(runs, "c" * 40, self.PR))
        self.assertIsNone(evidence_comment.select_gate_run([runs[0], runs[2], runs[3]], self.SHA, self.PR))

    def test_selection_is_scoped_to_the_pr(self):
        ours = self.run_data(2)
        other_pr = self.run_data(3, pull_requests=[{"number": 9}], head_branch="other")
        self.assertIs(evidence_comment.select_gate_run([ours, other_pr], self.SHA, self.PR), ours)

    def test_fork_runs_sharing_a_head_match_only_the_exact_pr_title(self):
        fork_run = self.run_data(2, pull_requests=[], head_repository={"full_name": "x/fork"},
                                 display_title="quality-gate for PR #7")
        other_pr = {**fork_run, "id": 3, "created_at": self.run_data(3)["created_at"],
                    "display_title": "quality-gate for PR #8"}
        self.assertIs(evidence_comment.select_gate_run([fork_run, other_pr], self.SHA, self.FORK_PR), fork_run)
        gone = {"number": 7, "head": {"sha": self.SHA, "ref": "feature", "repo": None}}
        self.assertIsNone(evidence_comment.select_gate_run([fork_run], self.SHA, gone))

    def test_fork_run_with_missing_or_inexact_title_is_not_selected(self):
        for title in (None, "The PR title", "quality-gate for PR #8", "quality-gate for PR #70",
                      "quality-gate for PR #7 extra", "quality-gate for PR #7\n"):
            with self.subTest(title=title):
                run = self.run_data(pull_requests=[], head_repository={"full_name": "x/fork"}, display_title=title)
                self.assertIsNone(evidence_comment.select_gate_run([run], self.SHA, self.FORK_PR))

    def test_fork_title_cannot_override_head_repository_branch_or_pr_list(self):
        fork_run = self.run_data(pull_requests=[], head_repository={"full_name": "x/fork"},
                                 display_title="quality-gate for PR #7")
        for overrides in ({"head_repository": {"full_name": "other/fork"}}, {"head_branch": "elsewhere"},
                          {"head_repository": {"full_name": "other/fork"}, "head_branch": "elsewhere"},
                          {"pull_requests": [{"number": 8}]}):
            with self.subTest(overrides=overrides):
                self.assertIsNone(evidence_comment.select_gate_run([{**fork_run, **overrides}], self.SHA, self.FORK_PR))

    def test_selection_uses_id_to_break_creation_time_ties_and_ignores_conclusion(self):
        for conclusion in ("success", "failure", "cancelled", "timed_out", "skipped", None):
            with self.subTest(conclusion=conclusion):
                newer = self.run_data(3, created_at=self.run_data()["created_at"], conclusion=conclusion)
                self.assertIs(evidence_comment.select_gate_run([newer, self.run_data()], self.SHA, self.PR), newer)

    def refresh_mocks(self, stack, *, runs=None, statuses=("completed",), conclusion="success", live=None, jobs=None,
                      rerun_error=None):
        pages = [{"workflow_runs": []}, {"workflow_runs": runs if runs is not None else [self.run_data()]}]
        jobs = jobs if jobs is not None else [{"id": 21, "name": "quality-gate", "conclusion": "success"}]
        calls = stack.enter_context(mock.patch.object(evidence_comment.qlib, "sh", side_effect=[
            json.dumps(pages), json.dumps([{"jobs": [{"id": 20, "name": "checks"}]}, {"jobs": jobs}]), rerun_error or ""]))
        polled = [{"status": status, "conclusion": conclusion if status == "completed" else None} for status in statuses]
        api = stack.enter_context(mock.patch.object(evidence_comment, "gh", side_effect=[
            *polled, {"head": {"sha": live or self.SHA}}]))
        sleep = stack.enter_context(mock.patch.object(evidence_comment.time, "sleep"))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return calls, api, sleep

    def test_refresh_reruns_only_selected_gate_after_completion_whatever_its_verdict(self):
        for conclusion in ("success", "failure", "timed_out", "skipped", "neutral", "action_required"):
            with self.subTest(conclusion=conclusion), contextlib.ExitStack() as stack:
                calls, api, sleep = self.refresh_mocks(stack, runs=[self.run_data(conclusion=conclusion)],
                    statuses=("queued", "in_progress", "completed"), conclusion=conclusion,
                    jobs=[{"id": 21, "name": "quality-gate", "conclusion": conclusion}])
                evidence_comment.refresh_gate("o/r", self.SHA, self.PR)
                self.assertEqual(api.call_args_list, [mock.call("repos/o/r/actions/runs/2")] * 3
                                 + [mock.call("repos/o/r/pulls/4")])
                self.assertEqual(sleep.call_args_list, [mock.call(15)] * 2)
                self.assertEqual(calls.call_args_list[0], mock.call("gh", "api", "--paginate", "--slurp",
                    f"repos/o/r/actions/workflows/quality-gate.yml/runs?event=pull_request&head_sha={self.SHA}&per_page=100"))
                self.assertEqual(calls.call_args_list[1], mock.call("gh", "api", "--paginate", "--slurp",
                    "repos/o/r/actions/runs/2/jobs?filter=latest&per_page=100"))
                self.assertEqual(calls.call_args_list[2], mock.call(
                    "gh", "run", "rerun", "2", "--repo", "o/r", "--job", "21"))

    def test_cancelled_run_is_not_rerun(self):
        with contextlib.ExitStack() as stack:
            calls, api, _ = self.refresh_mocks(stack, conclusion="cancelled")
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            evidence_comment.refresh_gate("o/r", self.SHA, self.PR)
            self.assertIn("superseded", output.getvalue())
            self.assertEqual(calls.call_count, 1)

    def test_refused_rerun_fails_with_actionable_message(self):
        with contextlib.ExitStack() as stack:
            error = SystemExit("command failed (1): gh run rerun 2 --repo o/r --job 21\nrun is older than 30 days")
            calls, _, _ = self.refresh_mocks(stack, rerun_error=error)
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit) as raised:
                evidence_comment.refresh_gate("o/r", self.SHA, self.PR)
            message = str(raised.exception)
            self.assertIn("evidence_comment: GitHub refused to re-run quality-gate.yml run 2", message)
            self.assertIn("push a commit or close and reopen PR #4 to start a fresh gate run", message)
            self.assertIn(str(error), message)
            self.assertEqual(calls.call_args, mock.call("gh", "run", "rerun", "2", "--repo", "o/r", "--job", "21"))
            self.assertNotIn("re-running quality-gate job", output.getvalue())

    def test_head_moving_while_waiting_skips_the_stale_rerun(self):
        with contextlib.ExitStack() as stack:
            calls, api, _ = self.refresh_mocks(stack, live="b" * 40)
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            evidence_comment.refresh_gate("o/r", self.SHA, self.PR)
            self.assertIn("PR head moved", output.getvalue())
            self.assertEqual(api.call_args_list[-1], mock.call("repos/o/r/pulls/4"))
            self.assertEqual(calls.call_count, 2)   # runs and jobs looked up; no re-run
            self.assertFalse(any(c.args[:3] == ("gh", "run", "rerun") for c in calls.call_args_list))

    def test_no_run_is_a_notice_without_polling_or_rerunning(self):
        with contextlib.ExitStack() as stack:
            calls, api, sleep = self.refresh_mocks(stack, runs=[])
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            evidence_comment.refresh_gate("o/r", self.SHA, self.PR)
            self.assertIn("::notice", output.getvalue())
            self.assertEqual(calls.call_count, 1)
            api.assert_not_called()
            sleep.assert_not_called()

    def test_wait_stops_at_twenty_minutes_and_does_not_rerun(self):
        with contextlib.ExitStack() as stack:
            calls, api, sleep = self.refresh_mocks(stack, statuses=["in_progress"] * 80)
            now = [0]
            stack.enter_context(mock.patch.object(evidence_comment.time, "monotonic", side_effect=lambda: now[0]))
            sleep.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
            with self.assertRaisesRegex(SystemExit, "::error.*run 2.*20 minutes"):
                evidence_comment.refresh_gate("o/r", self.SHA, self.PR)
            self.assertEqual(now[0], 1200)
            self.assertEqual(sleep.call_args_list, [mock.call(15)] * 80)
            self.assertEqual(api.call_count, 80)
            self.assertEqual(calls.call_count, 1)

    def test_missing_gate_job_fails_without_rerunning_other_jobs(self):
        with contextlib.ExitStack() as stack:
            calls, _, _ = self.refresh_mocks(stack, jobs=[])
            with self.assertRaisesRegex(SystemExit, "no quality-gate job"):
                evidence_comment.refresh_gate("o/r", self.SHA, self.PR)
            self.assertEqual(calls.call_count, 2)

    def test_publish_without_approval_refreshes_after_posting_replacement_evidence(self):
        with tempfile.TemporaryDirectory(prefix="kit-test-publish-") as tmp, contextlib.ExitStack() as stack:
            state = Path(tmp)
            (state / "packet.md").write_text("# replacement evidence")
            (state / "risk.json").write_text(json.dumps({"sha": self.SHA}))
            (state / f"review-{self.SHA[:12]}.json").write_text(json.dumps({"sha": self.SHA, "blocking": True}))
            stack.enter_context(mock.patch.object(evidence_comment.qlib, "state_dir", return_value=state))
            stack.enter_context(mock.patch.object(evidence_comment, "gh", return_value=self.PR))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            events = []
            def sh(*args):
                if args == ("git", "rev-parse", "HEAD"):
                    return self.SHA
                self.assertEqual(args[:3], ("gh", "pr", "comment"))
                self.assertIn('"blocking": true', Path(args[-1]).read_text())
                Path(args[-1]).unlink()
                events.append("comment")
                return ""
            stack.enter_context(mock.patch.object(evidence_comment.qlib, "sh", side_effect=sh))
            refresh = stack.enter_context(mock.patch.object(evidence_comment, "refresh_gate",
                side_effect=lambda *args: events.append("refresh")))
            evidence_comment.publish(argparse.Namespace(pr="4", repo="o/r", approve=False))
            self.assertEqual(events, ["comment", "refresh"])
            refresh.assert_called_once_with("o/r", self.SHA, self.PR)


class CiPolicy(unittest.TestCase):
    def test_defaults_and_overrides(self):
        d = Path(tempfile.mkdtemp(prefix="kit-test-ci-"))
        try:
            (d / "quality.toml").write_text("version = 1\n")
            self.assertEqual(ci_policy.read(str(d / "quality.toml")),
                             {"runs_on": "ubuntu-latest", "setup": [], "review_where": "ci", "bootstrap": False})
            (d / "quality.toml").write_text('version = 1\n[ci]\nruns_on = "macos-15"\nsetup = ["echo hi"]\n[review]\nwhere = "local"\n')
            p = ci_policy.read(str(d / "quality.toml"))
            self.assertEqual((p["runs_on"], p["setup"], p["review_where"]), ("macos-15", ["echo hi"], "local"))
            out = subprocess.run([sys.executable, str(KIT / "scripts" / "ci_policy.py"), str(d / "quality.toml")],
                                 check=True, capture_output=True, text=True).stdout
            self.assertEqual(out.split(), ["runs_on=macos-15", "review_where=local", "bootstrap=false"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_run_setup_runs_commands_in_order_and_stops_on_failure(self):
        d = Path(tempfile.mkdtemp(prefix="kit-test-ci-"))
        try:
            (d / "quality.toml").write_text(f'version = 1\n[ci]\nsetup = ["touch {d}/one", "false", "touch {d}/two"]\n')
            res = subprocess.run([sys.executable, str(KIT / "scripts" / "ci_policy.py"), str(d / "quality.toml"), "--run-setup"],
                                 capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0)
            self.assertTrue((d / "one").exists())
            self.assertFalse((d / "two").exists())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rejects_an_unknown_review_location(self):
        d = Path(tempfile.mkdtemp(prefix="kit-test-ci-"))
        try:
            (d / "quality.toml").write_text('version = 1\n[review]\nwhere = "somewhere"\n')
            with self.assertRaises(SystemExit):
                ci_policy.read(str(d / "quality.toml"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class EvidenceComments(unittest.TestCase):
    SHA = "a" * 40
    OTHER = "b" * 40

    def evidence(self, sha: str) -> dict:
        return {"risk": {"sha": sha, "tier": 1, "approvals_required": 1},
                "review": {"sha": sha, "blocking": False, "findings": []}}

    def comment(self, sha: str, login: str, created: str, kind: str = "User", body: str | None = None) -> dict:
        e = self.evidence(sha)
        return {"user": {"login": login, "type": kind}, "created_at": created,
                "body": body if body is not None else evidence_comment.render("# packet\n", e["risk"], e["review"])}

    def test_render_and_parse_round_trip(self):
        e = self.evidence(self.SHA)
        body = evidence_comment.render("# packet\n\nwith a ```json\n{\"decoy\": 1}\n``` block in the prose\n", e["risk"], e["review"])
        sha, data = evidence_comment.parse(body)
        self.assertEqual(sha, self.SHA)
        self.assertEqual(data, e)
        self.assertIsNone(evidence_comment.parse("just a comment"))
        self.assertIsNone(evidence_comment.parse(f"<!-- quality-evidence sha={self.SHA} -->\nno json"))

    def test_select_takes_the_newest_human_comment_for_the_head_only(self):
        humans = {"alice"}
        older = self.comment(self.SHA, "alice", "2026-11-13T10:00:00Z")
        newer = self.comment(self.SHA, "alice", "2026-11-13T11:00:00Z")
        newer["body"] = newer["body"].replace('"tier": 1', '"tier": 2')
        comments = [
            older,
            self.comment(self.SHA, "someone-else", "2026-11-13T12:00:00Z"),          # not in humans
            self.comment(self.SHA, "alice", "2026-11-13T12:00:00Z", kind="Bot"),     # a bot named like a human
            self.comment(self.OTHER, "alice", "2026-11-13T12:00:00Z"),               # another commit
            {"user": {"login": "alice", "type": "User"}, "created_at": "2026-11-13T12:00:00Z", "body": "LGTM"},
            newer,
        ]
        chosen = evidence_comment.select(comments, self.SHA, humans)
        self.assertEqual(chosen["risk"]["tier"], 2)
        self.assertIsNone(evidence_comment.select(comments, "c" * 40, humans))
        self.assertIsNone(evidence_comment.select(comments, self.SHA, {"nobody"}))

    def test_sign_off_is_one_the_gate_counts_for_who_you_are(self):
        cfg = {"risk": {"humans": ["Alice"], "director_counts_as_reviewer": True}}
        bot_pr = {"user": {"login": "dependabot[bot]"}, "labels": []}
        own_pr = {"user": {"login": "alice"}, "labels": [{"name": "agent-authored"}]}
        # Someone else's PR: a real approval (the gate ignores director reviews from non-authors).
        self.assertEqual(evidence_comment.sign_off("alice", bot_pr, cfg)[0], "APPROVE")
        # Your own PR: GitHub refuses author approvals, so the director review, which the gate reads.
        event, body, note = evidence_comment.sign_off("Alice", own_pr, cfg)
        self.assertEqual(event, "COMMENT")
        self.assertRegex(body, approval_gate.DIRECTOR_RE)
        self.assertIsNone(note)
        # Your own PR, but the gate won't count it: say so instead of failing silently.
        _, _, note = evidence_comment.sign_off("alice", {**own_pr, "labels": []}, cfg)
        self.assertIn("agent-authored", note)

    def test_inner_sha_must_match_the_marker(self):
        forged = self.comment(self.SHA, "alice", "2026-11-13T10:00:00Z")
        forged["body"] = forged["body"].replace(f'"sha": "{self.SHA}"', f'"sha": "{self.OTHER}"')
        self.assertIsNone(evidence_comment.select([forged], self.SHA, {"alice"}))

    def test_publish_approve_posts_identity_appropriate_review_on_evidenced_commit(self):
        cfg = {"risk": {"humans": ["Alice"], "director_counts_as_reviewer": True}}
        with tempfile.TemporaryDirectory(prefix="kit-test-publish-") as tmp:
            state = Path(tmp)
            data = self.evidence(self.SHA)
            (state / "packet.md").write_text("# packet\n")
            (state / "risk.json").write_text(json.dumps(data["risk"]))
            (state / f"review-{self.SHA[:12]}.json").write_text(json.dumps(data["review"]))
            for author, expected in [("alice", "COMMENT"), ("dependabot[bot]", "APPROVE")]:
                with self.subTest(author=author), contextlib.ExitStack() as stack:
                    pr = {"head": {"sha": self.SHA}, "user": {"login": author},
                          "labels": [{"name": "agent-authored"}]}
                    stack.enter_context(mock.patch.object(evidence_comment.qlib, "state_dir", return_value=state))
                    stack.enter_context(mock.patch.object(evidence_comment.qlib, "load_config", return_value=cfg))
                    stack.enter_context(mock.patch.object(evidence_comment, "gh", return_value=pr))
                    output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    refresh = stack.enter_context(mock.patch.object(evidence_comment, "refresh_gate"))
                    def sh(*args, **kwargs):
                        if args == ("git", "rev-parse", "HEAD"):
                            return self.SHA
                        if args == ("gh", "api", "user", "-q", ".login"):
                            return "Alice"
                        if args[:3] == ("gh", "pr", "comment"):
                            Path(args[-1]).unlink()  # publish's temporary packet
                        return ""
                    calls = stack.enter_context(mock.patch.object(evidence_comment.qlib, "sh", side_effect=sh))
                    evidence_comment.publish(argparse.Namespace(pr="4", repo="o/r", approve=True))
                    refresh.assert_not_called()
                    self.assertIn("review event triggers quality-gate-refresh.yml", output.getvalue())
                    reviews = [c.args for c in calls.call_args_list if "repos/o/r/pulls/4/reviews" in c.args]
                    self.assertEqual(len(reviews), 1)
                    self.assertEqual(reviews[0][:5], ("gh", "api", "-X", "POST", "repos/o/r/pulls/4/reviews"))
                    self.assertIn(f"commit_id={self.SHA}", reviews[0])
                    self.assertIn(f"event={expected}", reviews[0])
                    if expected == "COMMENT":
                        self.assertIn("body=/director-approve", reviews[0])


class ApprovalGateTier(unittest.TestCase):
    """In local mode risk.json comes from a PR comment, computed against the PR's own policy.
    The gate must re-derive the tier floor and the approval count from the trusted policy."""

    def setUp(self):
        with open(KIT / "quality.toml", "rb") as fh:
            self.cfg = tomllib.load(fh)
        self.cfg["risk"]["approvals"] = {"t0": 0, "t1": 1, "t2": 2}
        self.cfg["risk"]["allow_model_t0"] = False

    def gate(self, risk: dict, files: list[str]) -> tuple[int, int]:
        stats = {f: (1, 1) for f in files}
        floor, _, floor_hit = classify_risk.deterministic(self.cfg, files, stats)
        tier, need, _ = approval_gate.required(self.cfg, risk, floor, floor_hit, sum(a + r for a, r in stats.values()))
        return tier, need

    def test_a_pr_cannot_lower_its_own_tier_or_approvals(self):
        # The PR edits quality.toml to need no approvals and publishes evidence computed against it.
        forged = {"tier": 0, "approvals_required": 0}
        self.assertEqual(self.gate(forged, ["quality.toml"]), (2, 2))
        self.assertEqual(self.gate(forged, ["Sources/App/main.swift"]), (1, 1))

    def test_approvals_come_from_the_trusted_policy_not_the_evidence(self):
        self.assertEqual(self.gate({"tier": 2, "approvals_required": 0}, ["docs/a.md"]), (2, 2))
        self.assertEqual(self.gate({"tier": 0, "approvals_required": 5}, ["docs/a.md"]), (0, 0))

    def test_published_tier_can_raise_the_floor(self):
        self.assertEqual(self.gate({"tier": 2}, ["Sources/App/main.swift"]), (2, 2))

    def test_malformed_tier_fails_closed(self):
        for bad in [None, "0", True, -1, 7, 1.0]:
            self.assertEqual(self.gate({"tier": bad}, ["docs/a.md"]), (2, 2), repr(bad))

    def test_model_lowered_tier0_only_where_the_trusted_policy_allows_it(self):
        lowered = {"tier": 0, "model_t0": True}
        self.assertEqual(self.gate(lowered, ["Sources/App/flagged.swift"]), (1, 1))  # policy off
        self.cfg["risk"]["allow_model_t0"] = True
        self.assertEqual(self.gate(lowered, ["Sources/App/flagged.swift"]), (0, 0))
        self.assertEqual(self.gate(lowered, ["quality.toml"]), (2, 2))  # a floor rule hit
        big = [f"Sources/App/f{i}.swift" for i in range(self.cfg["risk"]["t0"]["max_changed_lines"])]
        self.assertEqual(self.gate(lowered, big), (1, 1))  # over the T0 size limit

    def test_gate_rejects_live_head_different_from_checkout_even_with_matching_evidence(self):
        head = "a" * 40
        cfg = {"_path": "trusted/quality.toml", "risk": {"humans": [], "approvals": {"t0": 0}}}
        with tempfile.TemporaryDirectory(prefix="kit-test-head-") as tmp, contextlib.ExitStack() as stack:
            risk, review = Path(tmp) / "risk.json", Path(tmp) / "review-head.json"
            risk.write_text(json.dumps({"sha": head, "tier": 0}))
            review.write_text(json.dumps({"sha": head, "blocking": False}))
            stack.enter_context(mock.patch.object(sys, "argv", ["approval_gate.py", "--pr", "4", "--repo", "o/r",
                                                                  "--base", "base", "--risk", str(risk), "--review", str(review)]))
            stack.enter_context(mock.patch.object(approval_gate.qlib, "load_config", return_value=cfg))
            stack.enter_context(mock.patch.object(approval_gate, "gh", return_value={"head": {"sha": head}, "user": {"login": "author"}}))
            stack.enter_context(mock.patch.object(approval_gate, "gh_list", return_value=[]))
            stack.enter_context(mock.patch.object(approval_gate.qlib, "numstat", return_value={}))
            stack.enter_context(mock.patch.object(approval_gate.qlib, "changed_files", return_value=[]))
            stack.enter_context(mock.patch.object(approval_gate.classify_risk, "deterministic", return_value=(0, [], False)))
            stack.enter_context(mock.patch.object(approval_gate, "policy_problem", return_value=None))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            with mock.patch.object(approval_gate.qlib, "sh", return_value=head):
                approval_gate.main()  # control: matching checkout, evidence and live head pass
            with mock.patch.object(approval_gate.qlib, "sh", return_value="b" * 40):
                with self.assertRaises(SystemExit) as stopped:
                    approval_gate.main()
                self.assertEqual(stopped.exception.code, 1)
            self.assertIn("checked-out commit", output.getvalue())


if __name__ == "__main__":
    unittest.main()
