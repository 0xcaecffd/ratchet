"""Self-test for tools/ratchet.py (placeholder values only).

    python -m unittest discover -s tests -v        # from the repo root
    python -m unittest -v                          # from inside tests/

Every test pins the config with $RATCHET_CONFIG so a ratchet.toml in some
parent directory of the checkout cannot change the result.
"""

from __future__ import annotations

import importlib.util
import json
import re
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "ratchet.py"


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("ratchet", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ratchet"] = mod
    spec.loader.exec_module(mod)
    return mod


al = load_module(TOOL)

# the keys of the workflow's tasks.jsonl index line, and nothing else
INDEX_KEYS = {"ts", "session", "repo", "task_id", "subject", "task_dir", "status",
              "rungs", "final_rung", "work_units", "findings", "wrong", "events"}

DEFAULT_CONFIG_TOML = """
# the default ladder, written out so discovery cannot reach a real config
[ladder]
opus55 = "claude-opus-5-5"
opus5 = "claude-opus-5"
opus48 = "claude-opus-4-8"
opus46 = "claude-opus-4-6"
sonnet55 = "claude-sonnet-5-5"
sonnet5 = "claude-sonnet-5"
sonnet46 = "claude-sonnet-4-6"

[outcomes]
climb = ["b", "b?", "c", "w", "unavailable"]
"""


class Base(unittest.TestCase):
    config_text = DEFAULT_CONFIG_TOML
    config_name = "ratchet.toml"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ratchet_"))
        self.cfg_path = self.tmp / self.config_name
        self.cfg_path.write_text(self.config_text, encoding="utf-8")
        self._old_env = os.environ.get(al.CONFIG_ENV)
        os.environ[al.CONFIG_ENV] = str(self.cfg_path)
        self.cfg = al.load_config()
        self.d = self.tmp / "t20261003-1"
        self.d.mkdir(parents=True, exist_ok=True)
        self.cli("init", "--dir", str(self.d))

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop(al.CONFIG_ENV, None)
        else:
            os.environ[al.CONFIG_ENV] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, check=True):
        rc = al.main([str(a) for a in args])
        if check:
            self.assertEqual(rc, 0, "ratchet.py %s" % " ".join(str(a) for a in args))
        return rc

    def jsonl(self, name):
        return al.read_jsonl(self.d / name)

    def work(self, unit, attempt, rung, verified, **kw):
        args = ["work", str(self.d), "--unit", unit, "--attempt", attempt, "--rung", rung,
                "--verified", verified]
        for k, v in kw.items():
            args += ["--" + k.replace("_", "-"), str(v)]
        return self.cli(*args)

    def close(self, unit, outcome, rung=None, k=None):
        args = ["attempt", "close", str(self.d), "--unit", unit, "--outcome", outcome]
        if rung:
            args += ["--rung", rung]
        if k is not None:
            args += ["--k", k]
        return self.cli(*args)

    def rung_for(self, unit="u1"):
        state = al.unit_state(self.d, self.cfg)
        return al.next_rung(state.get(unit, {"done": False, "wrong": False, "rungs": []}),
                            self.cfg)


class InitTest(Base):
    def test_init_dir_makes_the_four_files(self):
        for name in ("ledger.jsonl", "work.jsonl", "attempts.jsonl", "findings.md"):
            self.assertTrue((self.d / name).exists(), name)
        self.assertTrue((self.d / "out").is_dir())
        self.assertTrue((self.d / "findings.md").read_text(encoding="utf-8").startswith("# findings"))

    def test_init_repo_creates_dated_task_dir(self):
        root = self.tmp / "scratch"
        self.cli("init", "--repo", "docs-pipeline", "--root", str(root),
                 "--task-id", "t20261003-7")
        d = root / "docs-pipeline" / "tasks" / "t20261003-7"
        self.assertTrue((d / "attempts.jsonl").exists())
        # a second task the same day gets the next n
        tid = al.next_task_id(root / "docs-pipeline" / "tasks",
                              now=al.parse_ts("2026-10-03T00:00:00Z"))
        self.assertEqual(tid, "t20261003-8")

    def test_findings_header_carries_the_config_note(self):
        cfg = al.Config(findings_note="House rule: cite the row id.")
        d = self.tmp / "noted"
        d.mkdir()
        al.init_dir(d, cfg)
        text = (d / "findings.md").read_text(encoding="utf-8")
        self.assertIn("House rule: cite the row id.", text)
        self.assertNotIn("bulky or sensitive", text)


class AppendTest(Base):
    def test_tool_assigns_n_and_ts(self):
        self.work("u1", 1, "opus55", "self", output="out/a.json", method="read", control="hash")
        self.work("u2", 1, "opus55", "self", output="out/b.json", method="read")
        rows = self.jsonl("work.jsonl")
        self.assertEqual([r["n"] for r in rows], [1, 2])
        for r in rows:
            self.assertRegex(r["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertLessEqual(rows[0]["ts"], rows[1]["ts"])
        self.assertEqual(rows[0]["outputs"], ["out/a.json"])

    def test_caller_may_not_set_n_or_ts(self):
        with self.assertRaises(ValueError):
            al.append_jsonl(self.d / "work.jsonl", {"n": 9, "unit": "u1"})
        with self.assertRaises(ValueError):
            al.append_jsonl(self.d / "work.jsonl", {"ts": "2000-01-01T00:00:00Z"})

    def test_ts_never_goes_backwards(self):
        al.append_jsonl(self.d / "work.jsonl", {"unit": "u1"},
                        now=al.parse_ts("2026-10-03T12:00:00Z"))
        line = al.append_jsonl(self.d / "work.jsonl", {"unit": "u2"},
                               now=al.parse_ts("2026-10-03T11:00:00Z"))
        self.assertEqual(line["ts"], "2026-10-03T12:00:00Z")
        self.assertEqual(line["n"], 2)

    def test_torn_line_is_skipped(self):
        p = self.d / "attempts.jsonl"
        p.write_text('{"n": 1, "ts": "2026-10-03T12:00:00Z", "unit": "u1"}\n{"n": 2, "ts":\n',
                     encoding="utf-8")
        line = al.append_jsonl(p, {"unit": "u1"})
        self.assertEqual(line["n"], 2)
        self.assertEqual(len(al.read_jsonl(p)), 2)

    def test_attempt_numbers_itself_per_unit(self):
        self.cli("attempt", "open", str(self.d), "--unit", "u1", "--rung", "opus55")
        self.close("u1", "b")
        self.cli("attempt", "open", str(self.d), "--unit", "u1", "--rung", "opus5")
        ks = [r["k"] for r in self.jsonl("attempts.jsonl")]
        self.assertEqual(ks, [1, 1, 2])
        models = {r["rung"]: r["model"] for r in self.jsonl("attempts.jsonl")}
        self.assertEqual(models["opus55"], "claude-opus-5-5")
        self.assertEqual(models["opus5"], "claude-opus-5")

    def test_unknown_outcome_is_refused(self):
        with self.assertRaises(SystemExit):
            self.close("u1", "nonsense", rung="opus55")
        self.assertEqual(self.jsonl("attempts.jsonl"), [])


class LadderTest(Base):
    def test_permission_denials_never_climb(self):
        with self.assertRaises(ValueError):
            al.Config(climb=["b", "a"])
        self.close("u9", "a", rung="opus55")
        self.assertEqual(self.rung_for("u9"), "reshape-or-user")

    def test_fresh_unit_starts_at_the_top_rung(self):
        self.assertEqual(al.DEFAULT_LADDER["opus55"], "claude-opus-5-5")
        self.assertEqual(self.cfg.rungs[0], "opus55")
        self.assertEqual(self.rung_for("never-seen"), "opus55")

    def test_climb_sequence_with_d_a_and_unavailable(self):
        # b climbs
        self.close("u1", "b", rung="opus55")
        self.assertEqual(self.rung_for(), "opus5")
        # d repeats the same rung
        self.close("u1", "d", rung="opus5")
        self.assertEqual(self.rung_for(), "opus5")
        # unavailable climbs, and skips a rung already tried
        self.close("u1", "unavailable", rung="opus5")
        self.assertEqual(self.rung_for(), "opus48")
        # w climbs
        self.close("u1", "w", rung="opus48")
        self.assertEqual(self.rung_for(), "opus46")
        # c after a failed fix in place climbs
        self.close("u1", "c", rung="opus46")
        self.assertEqual(self.rung_for(), "sonnet55")
        # a is reshaped or handed over, whatever the rung
        self.close("u1", "a", rung="sonnet55")
        self.assertEqual(self.rung_for(), "reshape-or-user")

    def test_bottom_of_the_ladder_is_user(self):
        for rung in self.cfg.rungs:
            self.close("u2", "b", rung=rung)
        self.assertEqual(self.rung_for("u2"), "user")

    def test_verified_work_is_done(self):
        self.close("u3", "b", rung="opus55")
        self.work("u3", 2, "opus5", "self", output="out/u3.json", method="read")
        self.assertEqual(self.rung_for("u3"), "done")

    def test_next_cli_prints_the_rung(self):
        self.close("u4", "b?", rung="opus55")
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.cli("next", str(self.d), "--unit", "u4")
        self.assertEqual(buf.getvalue().strip(), "opus5")


class WorkTest(Base):
    def test_wrong_requires_corrects(self):
        with self.assertRaises(SystemExit):
            self.work("u1", 2, "parent", "wrong")
        self.assertEqual(self.jsonl("work.jsonl"), [])
        self.work("u1", 1, "opus55", "self", output="out/u1.json", method="read")
        self.work("u1", 2, "parent", "wrong", corrects=1, control="row count mismatch")
        rows = self.jsonl("work.jsonl")
        self.assertEqual(rows[-1]["corrects"], 1)
        self.assertEqual(rows[-1]["verified"], "wrong")
        s = al.unit_state(self.d, self.cfg)["u1"]
        self.assertTrue(s["wrong"])
        self.assertFalse(s["done"])


class FindingsTest(Base):
    SECRET = "zzsecretfindingtextzz"

    def populate(self):
        self.cli("find", str(self.d), "--attempt", 1, "--rung", "opus55", "--agent", "ab12cd34",
                 "--finding", "counted 3 widgets " + self.SECRET,
                 "--evidence", "out/step1/counts.json", "--status", "established")
        self.cli("find", str(self.d), "--attempt", 1, "--rung", "opus55",
                 "--finding", "second thing " + self.SECRET, "--evidence", "work#1",
                 "--status", "open")
        self.cli("find", str(self.d), "--attempt", 2, "--rung", "opus5",
                 "--finding", "corrected count " + self.SECRET,
                 "--evidence", "out/step1/counts.json",
                 "--supersedes", "F1.1", "--status", "established")
        self.cli("find", str(self.d), "--rung", "parent",
                 "--finding", "control reproduced " + self.SECRET, "--evidence", "ledger#7",
                 "--status", "refuted")

    def test_ids_and_section_headers(self):
        self.populate()
        text = (self.d / "findings.md").read_text(encoding="utf-8")
        self.assertIn("## A1 opus55 claude-opus-5-5 agent ab12cd34 ", text)
        self.assertIn("## A2 opus5 claude-opus-5 agent - ", text)
        self.assertIn("## P verification", text)
        self.assertEqual(text.count("## A1"), 1)
        parsed = al.parse_findings(self.d / "findings.md")
        self.assertEqual([f["id"] for f in parsed], ["F1.1", "F1.2", "F2.1", "FP.1"])
        self.assertEqual(parsed[2]["supersedes"], "F1.1")
        self.assertEqual([f["status"] for f in parsed],
                         ["established", "open", "established", "refuted"])

    def test_resume_is_value_free(self):
        self.populate()
        self.work("u1", 1, "opus55", "self", output="out/u1.json", method="read")
        self.close("u1", "done", rung="opus55")
        self.close("u2", "b", rung="opus55")
        g = al.digest(self.d, self.cfg)
        both = json.dumps(g) + "\n" + al.render_digest(g)
        self.assertNotIn(self.SECRET, both)
        self.assertEqual([f["id"] for f in g["findings"]], ["F1.1", "F1.2", "F2.1", "FP.1"])
        self.assertEqual(g["counts"], {"units_done": 1, "units_wrong": 0,
                                       "findings": 4, "attempts": 2})
        self.assertEqual(g["units_done"][0]["outputs"], ["out/u1.json"])
        self.assertEqual(g["units_done"][0]["work_n"], 1)
        self.assertEqual(g["next"], {"u2": "opus5"})
        self.assertEqual(g["rungs"]["u1"], [{"rung": "opus55", "outcome": "done"}])


class IndexTest(Base):
    def test_index_line_has_only_the_planned_keys(self):
        self.work("u1", 1, "opus55", "self", output="out/u1.json", method="read")
        self.close("u1", "done", rung="opus55")
        self.close("u2", "w", rung="opus55")
        g = al.digest(self.d, self.cfg)
        gj = self.tmp / "resume.json"
        gj.write_text(json.dumps(g), encoding="utf-8")
        ledger = self.tmp / "ledger" / "tasks.jsonl"
        self.cli("index", "--from-json", str(gj), "--repo-ledger", str(ledger),
                 "--repo", "docs-pipeline", "--task-id", "t20261003-1",
                 "--subject", "two units, forced w", "--task-dir", str(self.d),
                 "--session", "41f2bd2a", "--events", "forced w on unit 2")
        rows = al.read_jsonl(ledger)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), INDEX_KEYS)
        self.assertEqual(set(al.INDEX_KEYS), INDEX_KEYS)
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["rungs"], ["opus55:done", "opus55:w"])
        self.assertEqual(rows[0]["final_rung"], "opus55")
        self.assertEqual(rows[0]["work_units"], 1)
        self.assertEqual(rows[0]["events"], ["forced w on unit 2"])
        self.assertRegex(rows[0]["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_index_line_records_the_task_dir(self):
        g = al.digest(self.d, self.cfg)
        gj = self.tmp / "resume.json"
        gj.write_text(json.dumps(g), encoding="utf-8")
        ledger = self.tmp / "ledger" / "tasks.jsonl"
        self.cli("index", "--from-json", str(gj), "--repo-ledger", str(ledger),
                 "--repo", "r", "--task-id", "t1", "--subject", "s", "--task-dir", "/tmp/t1")
        rows = al.read_jsonl(ledger)
        self.assertEqual(rows[0]["task_dir"], "/tmp/t1")

    def test_status_user_when_a_unit_bottoms_out(self):
        for rung in self.cfg.rungs:
            self.close("u1", "b", rung=rung)
        g = al.digest(self.d, self.cfg)
        line = al.index_line(g, "pipeline", "t20261003-2", "s", "/tmp/x", session="deadbeef")
        self.assertEqual(line["status"], "user")
        self.assertEqual(set(line) | {"ts"}, INDEX_KEYS)

    def test_status_done_when_nothing_is_open(self):
        self.work("u1", 1, "opus55", "parent", output="out/u1.json", method="read")
        g = al.digest(self.d, self.cfg)
        self.assertEqual(al.index_line(g, "pipeline", "t1", "s", "/tmp/x")["status"], "done")

    def test_session_comes_from_the_configured_env_var(self):
        cfg = al.Config(session_env=["DEMO_RUN_ID"])
        old = os.environ.get("DEMO_RUN_ID")
        os.environ["DEMO_RUN_ID"] = "demo-7"
        try:
            line = al.index_line(al.digest(self.d, cfg), "p", "t1", "s", "/tmp/x", cfg=cfg)
        finally:
            if old is None:
                os.environ.pop("DEMO_RUN_ID", None)
            else:
                os.environ["DEMO_RUN_ID"] = old
        self.assertEqual(line["session"], "demo-7")


SHORT_LADDER_TOML = """
# a two-rung ladder with its own climb set
[ladder]
big = "claude-opus-5"
small = "claude-sonnet-5"

[outcomes]
climb = ["stopped", "rejected"]

[tasks]
root = "/var/tmp/demo"

[session]
env = ["DEMO_RUN_ID", "CI_JOB_ID"]

[findings]
note = "Cite the dataset row id; raw rows stay in `out/`.\\n"
"""

SHORT_LADDER_JSON = {
    "ladder": {"big": "claude-opus-5", "small": "claude-sonnet-5"},
    "outcomes": {"climb": ["stopped", "rejected"]},
    "tasks": {"root": "/var/tmp/demo"},
}


class ConfigTest(unittest.TestCase):
    """The ladder, the climb set and the rest come from the config file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ratchet_cfg_"))
        self._old_env = os.environ.get(al.CONFIG_ENV)
        os.environ.pop(al.CONFIG_ENV, None)

    def tearDown(self):
        if self._old_env is not None:
            os.environ[al.CONFIG_ENV] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def task_dir(self, cfg_text, name="ratchet.toml"):
        p = self.tmp / name
        p.write_text(cfg_text, encoding="utf-8")
        d = self.tmp / "t1"
        d.mkdir(exist_ok=True)
        cfg = al.load_config(str(p))
        al.init_dir(d, cfg)
        return cfg, d, p

    def test_defaults_when_no_config_is_found(self):
        cfg = al.load_config(start=self.tmp, env={})
        if cfg.path is not None:          # a real ~/.config file on this host
            self.skipTest("a user config exists at %s" % cfg.path)
        self.assertEqual(cfg.rungs, list(al.DEFAULT_LADDER))
        self.assertEqual(cfg.climb, al.DEFAULT_CLIMB)
        self.assertEqual(cfg.task_root, al.DEFAULT_TASK_ROOT)

    def test_short_toml_ladder_drives_the_climb(self):
        cfg, d, p = self.task_dir(SHORT_LADDER_TOML)
        self.assertEqual(cfg.rungs, ["big", "small"])
        self.assertEqual(cfg.climb, ("stopped", "rejected"))
        self.assertEqual(cfg.task_root, "/var/tmp/demo")
        self.assertEqual(cfg.session_env, ("DEMO_RUN_ID", "CI_JOB_ID"))
        self.assertIn("Cite the dataset row id", (d / "findings.md").read_text(encoding="utf-8"))
        self.assertEqual(cfg.outcomes, ("done", "stopped", "rejected", "a", "d"))
        # the custom climb outcome moves one rung, then bottoms out at `user`
        al.main(["--config", str(p), "attempt", "close", str(d), "--unit", "u1",
                 "--outcome", "stopped", "--rung", "big"])
        self.assertEqual(al.unit_state(d, cfg)["u1"]["next"], "small")
        al.main(["--config", str(p), "attempt", "close", str(d), "--unit", "u1",
                 "--outcome", "rejected", "--rung", "small"])
        self.assertEqual(al.unit_state(d, cfg)["u1"]["next"], "user")
        # the model id of a rung comes from the config
        rows = al.read_jsonl(d / "attempts.jsonl")
        self.assertEqual(rows[0]["model"], "claude-opus-5")
        self.assertEqual(rows[1]["model"], "claude-sonnet-5")

    def test_default_outcomes_are_unknown_to_a_custom_climb_set(self):
        cfg, d, p = self.task_dir(SHORT_LADDER_TOML)
        with self.assertRaises(SystemExit):
            al.main(["--config", str(p), "attempt", "close", str(d), "--unit", "u1",
                     "--outcome", "b", "--rung", "big"])

    def test_json_config_is_equivalent(self):
        cfg, d, p = self.task_dir(json.dumps(SHORT_LADDER_JSON), name="ratchet.json")
        self.assertEqual(cfg.rungs, ["big", "small"])
        self.assertEqual(cfg.climb, ("stopped", "rejected"))
        self.assertEqual(cfg.task_root, "/var/tmp/demo")
        self.assertEqual(cfg.session_env, al.DEFAULT_SESSION_ENV)   # not set: default

    def test_config_env_var_and_parent_discovery(self):
        (self.tmp / "ratchet.toml").write_text(SHORT_LADDER_TOML, encoding="utf-8")
        deep = self.tmp / "a" / "b"
        deep.mkdir(parents=True)
        found = al.find_config(start=deep, env={})
        self.assertEqual(found, self.tmp / "ratchet.toml")
        other = self.tmp / "other.toml"
        other.write_text("[ladder]\nonly = \"claude-sonnet-5\"\n", encoding="utf-8")
        self.assertEqual(al.find_config(start=deep, env={al.CONFIG_ENV: str(other)}), other)
        self.assertEqual(al.load_config(start=deep, env={al.CONFIG_ENV: str(other)}).rungs,
                         ["only"])
        # an explicit --config wins over the environment
        self.assertEqual(al.find_config(str(self.tmp / "ratchet.toml"),
                                        env={al.CONFIG_ENV: str(other)}),
                         self.tmp / "ratchet.toml")

    def test_ladder_as_an_ordered_list_of_pairs(self):
        cfg = al.config_from_data({"ladder": [["first", "m1"], ["second", "m2"]]})
        self.assertEqual(cfg.rungs, ["first", "second"])
        self.assertEqual(cfg.model_for("second"), "m2")

    def test_bad_config_reports_its_path(self):
        p = self.tmp / "ratchet.json"
        p.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            al.load_config(str(p))
        self.assertIn("ratchet.json", str(cm.exception))

    def test_missing_explicit_config_is_an_error(self):
        with self.assertRaises(ValueError):
            al.load_config(str(self.tmp / "nope.toml"))

    def test_fallback_toml_reader_matches_the_fields_we_use(self):
        """Both readers (tomllib, and the 3.9/3.10 fallback) see the same config."""
        for use_tomllib in (True, False):
            saved = al.tomllib
            if not use_tomllib:
                al.tomllib = None
            try:
                data = al.read_toml(SHORT_LADDER_TOML)
                cfg = al.config_from_data(data)
            finally:
                al.tomllib = saved
            with self.subTest(tomllib=use_tomllib):
                self.assertEqual(data["ladder"],
                                 {"big": "claude-opus-5", "small": "claude-sonnet-5"})
                self.assertEqual(data["outcomes"]["climb"], ["stopped", "rejected"])
                self.assertEqual(data["tasks"]["root"], "/var/tmp/demo")
                self.assertEqual(data["session"]["env"], ["DEMO_RUN_ID", "CI_JOB_ID"])
                self.assertEqual(cfg.rungs, ["big", "small"])
                self.assertTrue(cfg.findings_note.endswith("\n"))


class VersionTest(unittest.TestCase):
    def test_version_is_the_file_sha256(self):
        import contextlib, hashlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            al.main(["--version"])
        self.assertEqual(buf.getvalue().strip(),
                         hashlib.sha256(TOOL.read_bytes()).hexdigest())
        self.assertRegex(buf.getvalue().strip(), r"^[0-9a-f]{64}$")


class GenericTextTest(unittest.TestCase):
    """The tool stays exportable: no hard-coded hosts or absolute paths."""

    def test_no_hardcoded_environment(self):
        text = TOOL.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), "an IP address")
        for frag in ("/srv/", "/opt/", "C:\\Users"):
            self.assertNotIn(frag, text, "tools/ratchet.py hard-codes %r" % frag)


if __name__ == "__main__":
    unittest.main()
