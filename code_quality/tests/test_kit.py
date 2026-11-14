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
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
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
                        ".github/PULL_REQUEST_TEMPLATE.md", ".claude/settings.json", ".gitignore"):
                self.assertTrue((r.path / rel).exists(), rel)
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
            self.install(r.path, "team")
            self.assertEqual((r.path / "CLAUDE.md").read_text(), "# my repo rules\n")
            self.assertEqual((r.path / "quality.toml").read_text(), 'version = 1\n[checks]\nfast = ["true"]\nfull = ["true"]\n')
            self.assertEqual((r.path / ".gitignore").read_text().count(".quality/"), 1)
            self.assertTrue(own_case.exists(), "the repo's own eval cases survive an update")
            self.assertFalse(stale.exists(), "kit files removed upstream are removed on update")
            # A source-only case that an older installer copied in is removed on update.
            for name in install.internal_cases():
                self.assertFalse((r.path / "code_quality" / "evals" / "cases" / name).exists(), name)
            self.assertTrue((r.path / ".github" / "CODEOWNERS").exists())

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
        check = staging.index("if [ -f _trusted/BOOTSTRAP ]")
        first_script = staging.index("_trusted/code_quality/scripts/")
        self.assertLess(check, first_script, "the bootstrap check must come before any staged script runs")
        self.assertIn("exit 1", staging[check:first_script])
        # The policy job reads the marker itself, not through a staged (possibly PR-supplied) script.
        policy = wf[wf.index("\n  policy:"):wf.index("\n  checks:")]
        self.assertIn('if [ -f _trusted/BOOTSTRAP ]; then echo "bootstrap=true"', policy)
        self.assertIn("grep -v '^bootstrap='", policy)


def qlib_policy(toml: Path, rules: Path) -> str:
    import qlib
    return qlib.policy_fingerprint(toml, rules)


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

    def test_inner_sha_must_match_the_marker(self):
        forged = self.comment(self.SHA, "alice", "2026-11-13T10:00:00Z")
        forged["body"] = forged["body"].replace(f'"sha": "{self.SHA}"', f'"sha": "{self.OTHER}"')
        self.assertIsNone(evidence_comment.select([forged], self.SHA, {"alice"}))


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


if __name__ == "__main__":
    unittest.main()
