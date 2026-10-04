"""Ratchet: a task's completed work, its findings, and a model-ladder retry.

One stdlib-only helper for any delegated-agent workflow. It writes the four
per-task files that live in the task dir, outside git:

    ledger.jsonl    the per-action log (a workflow may keep writing its own)
    work.jsonl      one line per completed reusable unit          (the agent writes)
    findings.md     append-only findings, one section per attempt (the agent writes)
    attempts.jsonl  one line when an attempt opens and one when it closes (the parent)
    out/            everything bulky or sensitive; findings cite it by path

Usage
-----
    ratchet.py init --dir DIR
    ratchet.py init --repo NAME [--root ROOT] [--task-id t20261003-2]
        `init --dir <dir>` adds the missing files to an existing dir.
        `--repo` creates <root>/<name>/tasks/t<YYYYMMDD>-<n>/ and prints its path.

    ratchet.py work DIR --unit U --attempt K --rung RUNG --agent ID8 --phase P \
        [--output PATH ...] --method TEXT --verified self|parent|no|wrong \
        [--control TEXT] [--corrects N]
        `--verified wrong` requires `--corrects N` (the work line it corrects).

    ratchet.py find DIR --attempt K --rung RUNG [--model ID] [--agent ID8] \
        --finding TEXT [--evidence TEXT] [--status established|open|refuted] \
        [--supersedes F1.2]
        Assigns the next id F<k>.<i> and opens `## A<k> <rung> <model> agent <id8> <ts>`
        when that attempt has no section yet. `--rung parent` writes into
        `## P verification` instead (ids FP.<i> unless --attempt says otherwise).

    ratchet.py attempt open  DIR --unit U --rung RUNG [--k K] [--model ID] \
        [--agent ID8] [--shape TEXT] [--work-from N ...] [--findings-from TEXT]
    ratchet.py attempt close DIR --unit U --outcome done|b|b?|c|w|a|d [--k K] \
        [--rung RUNG] [--shape TEXT] ...

    ratchet.py resume DIR [--json]      value-free digest; never finding text
    ratchet.py next DIR --unit U        the next ladder rung for that unit
    ratchet.py index --from-json FILE|- --repo-ledger PATH --repo R --task-id ID \
        --subject TEXT --task-dir PATH [--session ID] [--events TEXT ...]
    ratchet.py config [--json]          the effective config and where it came from
    ratchet.py --version                the sha256 of this file

Configuration
-------------
Everything workflow-specific comes from a config file, found in this order:
`--config PATH`, `$RATCHET_CONFIG`, `ratchet.toml`/`ratchet.json` in the cwd or
any parent, then `~/.config/ratchet/ratchet.toml`/`.json`. With none found the
defaults below apply. TOML is read with `tomllib` on Python 3.11+, else with the
small reader in this file (the subset `templates/ratchet.toml` uses).

Configurable: the ladder (ordered rung label -> model id), which outcomes climb a
rung, the task root for `init --repo`, the session environment variables, and the
note in the `findings.md` header. The default ladder is Opus 5.5, 5, 4.8, 4.6 then
Sonnet 5.5, 5, 4.6, and then `user`.

Outcomes in the climb set move the failing unit one rung down; `d` repeats the same
rung (resume after a session interruption); `a` prints `reshape-or-user`; a unit with
verified work prints `done`; a fresh unit starts at the top rung. A model never gets
a second attempt at the same unit except to resume after class d.

`n` and `ts` belong to the tool: `n` is the last line's n + 1, `ts` is the current UTC
time to the second and never earlier than the last line's. The append holds an
exclusive lock where the OS offers one (fcntl), and a torn or corrupt line is skipped
when reading. Caller-supplied JSON may not set either field.

Findings and digests carry counts, offsets, ids and paths only; bulky or sensitive
text stays in files under `out/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:                      # Windows: the lock is simply not taken
    fcntl = None

try:
    import tomllib                       # Python 3.11+
except ImportError:                      # pragma: no cover - 3.9/3.10 use the reader below
    tomllib = None

PROG = "ratchet.py"

# rung label -> model id. `user` ends the ladder and has no model.
DEFAULT_LADDER = {
    "opus55": "claude-opus-5-5",
    "opus5": "claude-opus-5",
    "opus48": "claude-opus-4-8",
    "opus46": "claude-opus-4-6",
    "sonnet55": "claude-sonnet-5-5",
    "sonnet5": "claude-sonnet-5",
    "sonnet46": "claude-sonnet-4-6",
}
USER = "user"
# outcomes of a closed attempt that move the unit one rung down
DEFAULT_CLIMB = ("b", "b?", "c", "w", "unavailable")
# outcomes that are never climbs: `done` ends the unit, `a` reshapes, `d` resumes
FIXED_OUTCOMES = ("done", "a", "d")
DEFAULT_TASK_ROOT = "."
DEFAULT_SESSION_ENV = ("RATCHET_SESSION",)
DEFAULT_FINDINGS_NOTE = (
    "Counts, offsets, ids and paths only: bulky or sensitive content goes in `out/`\n"
    "and is cited by path.\n"
)

VERIFIED = ("self", "parent", "no", "wrong")
STATUSES = ("established", "open", "refuted")
TS_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%S.%fZ")

WORK = "work.jsonl"
ATTEMPTS = "attempts.jsonl"
FINDINGS = "findings.md"
LEDGER = "ledger.jsonl"

FINDINGS_PREAMBLE = (
    "# findings\n"
    "\n"
    "Append-only, written as each thing is learned. One section per attempt\n"
    "(`## A<k> <rung> <model-id> agent <id8> <ts>`); the parent verifies under\n"
    "`## P verification`. Entries are\n"
    "`- F<k>.<i> <finding> - evidence: <path|ledger#n|work#n> - established|open|refuted`,\n"
    "and a correction is a new entry that says `supersedes F<k>.<i>`.\n"
)

ID_RE = re.compile(r"^- (F(?:\d+|P)\.\d+)\s")
SECTION_RE = re.compile(r"^## (?:A(\d+)|P)\b")

CONFIG_ENV = "RATCHET_CONFIG"
CONFIG_NAMES = ("ratchet.toml", "ratchet.json")


# -------------------------------------------------------------------------- config


class Config:
    """The workflow-specific settings; defaults when no config file is found."""

    def __init__(self, ladder=None, climb=None, task_root=None, session_env=None,
                 findings_note=None, path=None):
        self.ladder = dict(ladder) if ladder else dict(DEFAULT_LADDER)
        if not self.ladder:
            raise ValueError("config: ladder must have at least one rung")
        self.rungs = list(self.ladder)      # ordered; dicts keep insertion order on 3.7+
        self.climb = tuple(climb) if climb is not None else tuple(DEFAULT_CLIMB)
        self.task_root = task_root or DEFAULT_TASK_ROOT
        self.session_env = tuple(session_env) if session_env else tuple(DEFAULT_SESSION_ENV)
        self.findings_note = (DEFAULT_FINDINGS_NOTE if findings_note is None
                              else findings_note)
        self.path = Path(path) if path else None

    @property
    def outcomes(self) -> "tuple[str, ...]":
        seen = ["done"] + list(self.climb) + ["a", "d"]
        return tuple(dict.fromkeys(seen))

    @property
    def findings_header(self) -> str:
        note = self.findings_note or ""
        if note and not note.endswith("\n"):
            note += "\n"
        return FINDINGS_PREAMBLE + note

    def model_for(self, rung: str) -> str:
        return self.ladder.get(rung, rung)

    def as_dict(self) -> dict:
        return {"config_path": self.path.as_posix() if self.path else None,
                "ladder": dict(self.ladder), "climb": list(self.climb),
                "task_root": self.task_root, "session_env": list(self.session_env),
                "findings_note": self.findings_note}


DEFAULTS = Config()


def _toml_value(raw: str):
    """One TOML scalar or array of scalars, for the reader used before 3.11."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        body, out, buf, quote = raw[1:-1], [], "", ""
        for ch in body:
            if quote:
                buf += ch
                if ch == quote:
                    quote = ""
                continue
            if ch in "\"'":
                quote = ch
                buf += ch
                continue
            if ch == ",":
                if buf.strip():
                    out.append(_toml_value(buf))
                buf = ""
                continue
            buf += ch
        if buf.strip():
            out.append(_toml_value(buf))
        return out
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        s = raw[1:-1]
        if raw[0] == '"':
            for a, b in (("\\n", "\n"), ("\\t", "\t"), ('\\"', '"'), ("\\\\", "\\")):
                s = s.replace(a, b)
        return s
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def _strip_comment(line: str) -> str:
    out, quote = "", ""
    for ch in line:
        if quote:
            out += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            out += ch
            continue
        if ch == "#":
            break
        out += ch
    return out


def read_toml(text: str) -> dict:
    """tomllib when it exists, else a reader for tables and `key = scalar|array`."""
    if tomllib is not None:
        return tomllib.loads(text)
    data: dict = {}
    table = data
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            table = data
            for part in line[1:-1].split("."):
                part = part.strip().strip("\"'")
                nxt = table.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    table[part] = nxt
                table = nxt
            continue
        key, sep, val = line.partition("=")
        if not sep:
            raise ValueError("cannot read TOML line %r" % raw)
        table[key.strip().strip("\"'")] = _toml_value(val)
    return data


def _pick(data: dict, *paths):
    """The first present value among dotted paths, else None."""
    for path in paths:
        cur = data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur is not None:
            return cur
    return None


def _as_list(v) -> "list[str] | None":
    if v is None:
        return None
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def _as_ladder(v) -> "dict[str, str] | None":
    """A table of rung -> model, or an ordered list of [rung, model] pairs."""
    if v is None:
        return None
    if isinstance(v, dict):
        return {str(k): str(m) for k, m in v.items()}
    out = {}
    for item in v:
        if isinstance(item, dict):
            rung = item.get("rung") or item.get("label") or item.get("name")
            out[str(rung)] = str(item.get("model", ""))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            out[str(item[0])] = str(item[1])
        else:
            raise ValueError("ladder entries must be rung/model pairs")
    return out


def config_from_data(data: dict, path=None) -> Config:
    if not isinstance(data, dict):
        raise ValueError("the top level must be a table/object")
    return Config(
        ladder=_as_ladder(_pick(data, "ladder", "ladder.rungs")),
        climb=_as_list(_pick(data, "outcomes.climb", "climb", "ladder.climb")),
        task_root=_pick(data, "tasks.root", "task_root", "root"),
        session_env=_as_list(_pick(data, "session.env", "session_env")),
        findings_note=_pick(data, "findings.note", "findings_note"),
        path=path,
    )


def parse_config_file(path: Path) -> Config:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix.lower() == ".json" else read_toml(text)
    return config_from_data(data, path)


def find_config(explicit=None, start=None, env=None) -> "Path | None":
    """The config file by the documented search order, or None for the defaults."""
    env = os.environ if env is None else env
    if explicit:
        return Path(explicit)
    if env.get(CONFIG_ENV):
        return Path(env[CONFIG_ENV])
    here = Path(start) if start else Path.cwd()
    try:
        here = here.resolve()
    except OSError:                                  # pragma: no cover - defensive
        pass
    for d in (here,) + tuple(here.parents):
        for name in CONFIG_NAMES:
            if (d / name).is_file():
                return d / name
    home = Path.home() / ".config" / "ratchet"
    for name in CONFIG_NAMES:
        if (home / name).is_file():
            return home / name
    return None


def load_config(explicit=None, start=None, env=None) -> Config:
    path = find_config(explicit, start, env)
    if path is None:
        return Config()
    if not path.is_file():
        raise ValueError("no such config file: %s" % path)
    try:
        return parse_config_file(path)
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError("%s: %s" % (path, e))


def config_for(a: argparse.Namespace) -> Config:
    return load_config(getattr(a, "config", None))


# --------------------------------------------------------------------------- time


def parse_ts(ts: str) -> "datetime | None":
    for f in TS_FORMATS:
        try:
            return datetime.strptime(ts, f).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def env_session(cfg: "Config | None" = None) -> "str | None":
    for name in (cfg or DEFAULTS).session_env:
        if os.environ.get(name):
            return os.environ[name]
    return None


# ----------------------------------------------------------------------- jsonl io


def read_jsonl(path: Path) -> "list[dict]":
    """Every well-formed object in a jsonl file; a torn or corrupt line is skipped."""
    out = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return out
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def last_line(text: str) -> "tuple[int, datetime | None]":
    """The highest n and the latest ts in a jsonl file (torn lines skipped)."""
    n, latest = 0, None
    for raw in text.splitlines():
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        if isinstance(d.get("n"), int):
            n = max(n, d["n"])
        t = parse_ts(d.get("ts", ""))
        if t is not None:
            latest = t if latest is None or t > latest else latest
    return n, latest


def append_jsonl(path: "str | Path", fields: dict, now: "datetime | None" = None,
                 with_n: bool = True) -> dict:
    """Append one object, with the tool's own n and ts, under an exclusive lock.

    `with_n=False` stamps only ts: the index line has no n.
    """
    for k in ("n", "ts"):
        if k in fields:
            raise ValueError("the tool assigns %r, a caller may not set it" % k)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            text = fh.read()
            n, latest = last_line(text)
            t = (now or datetime.now(timezone.utc)).replace(microsecond=0)
            if latest is not None and t < latest:
                t = latest              # clock stepped back: never go below the last line
            line = {"n": n + 1, "ts": fmt(t)} if with_n else {"ts": fmt(t)}
            line.update(fields)
            if text and not text.endswith("\n"):
                fh.write("\n")
            fh.write(json.dumps(line) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
    return line


def append_text(path: "str | Path", text: str) -> None:
    """Append to a text file under the same lock, so two writers cannot interleave."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0, os.SEEK_END)
            if fh.tell():
                fh.seek(0)
                if not fh.read().endswith("\n"):
                    fh.write("\n")
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------- init


def init_dir(d: "str | Path", cfg: "Config | None" = None) -> "list[str]":
    """Create the files a task dir needs; existing files are left alone."""
    cfg = cfg or DEFAULTS
    d = Path(d)
    made = []
    (d / "out").mkdir(parents=True, exist_ok=True)
    if not (d / "out").is_dir():                      # pragma: no cover - defensive
        raise OSError("could not create %s" % (d / "out"))
    for name in (LEDGER, WORK, ATTEMPTS):
        p = d / name
        if not p.exists():
            p.touch()
            made.append(name)
    p = d / FINDINGS
    if not p.exists():
        p.write_text(cfg.findings_header, encoding="utf-8")
        made.append(FINDINGS)
    return made


def next_task_id(tasks_dir: Path, now: "datetime | None" = None) -> str:
    """t<YYYYMMDD>-<n>, n counting from 1 per day in that tasks dir."""
    day = (now or datetime.now(timezone.utc)).strftime("%Y%m%d")
    n = 0
    tasks_dir = Path(tasks_dir)
    if tasks_dir.is_dir():
        for child in tasks_dir.iterdir():
            m = re.match(r"^t%s-(\d+)$" % day, child.name)
            if m:
                n = max(n, int(m.group(1)))
    return "t%s-%d" % (day, n + 1)


def cmd_init(a: argparse.Namespace) -> int:
    cfg = config_for(a)
    if a.dir and a.repo:
        sys.exit("give either --dir or --repo, not both")
    if a.dir:
        d = Path(a.dir)
        if not d.is_dir():
            sys.exit("no such dir: %s (use --repo to create a task dir)" % d)
    elif a.repo:
        tasks = Path(a.root or cfg.task_root).expanduser() / a.repo / "tasks"
        task_id = a.task_id or next_task_id(tasks)
        d = tasks / task_id
        d.mkdir(parents=True, exist_ok=True)
    else:
        sys.exit("give --dir DIR or --repo NAME")
    init_dir(d, cfg)
    print(d.as_posix())
    return 0


# ---------------------------------------------------------------------------- work


def cmd_work(a: argparse.Namespace) -> int:
    if a.verified == "wrong" and a.corrects is None:
        sys.exit("--verified wrong requires --corrects N (the work line it corrects)")
    fields = {
        "attempt": a.attempt,
        "rung": a.rung,
        "agent": a.agent or "",
        "unit": a.unit,
        "phase": a.phase or "",
        "outputs": list(a.output or []),
        "method": a.method or "",
        "verified": a.verified,
        "control": a.control or "",
    }
    if a.corrects is not None:
        fields["corrects"] = a.corrects
    line = append_jsonl(Path(a.dir) / WORK, fields)
    print("work #%d %s unit=%s verified=%s" % (line["n"], line["ts"], a.unit, a.verified))
    return 0


# ------------------------------------------------------------------------ findings


def parse_findings(path: Path) -> "list[dict]":
    """Finding ids with their status and supersedes; never the finding text."""
    out = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return out
    attempt = None
    for raw in text.splitlines():
        m = SECTION_RE.match(raw)
        if m:
            attempt = m.group(1) or "P"
            continue
        m = ID_RE.match(raw)
        if not m:
            continue
        status = None
        for s in STATUSES:
            if re.search(r"\b%s\s*$" % s, raw):
                status = s
                break
        sup = re.search(r"supersedes\s+(F(?:\d+|P)\.\d+)", raw)
        out.append({"id": m.group(1), "attempt": attempt,
                    "status": status or "unknown",
                    "supersedes": sup.group(1) if sup else None})
    return out


def next_finding_id(path: Path, k: str) -> str:
    used = [f["id"] for f in parse_findings(path)]
    i = 0
    for fid in used:
        m = re.match(r"^F%s\.(\d+)$" % re.escape(k), fid)
        if m:
            i = max(i, int(m.group(1)))
    return "F%s.%d" % (k, i + 1)


def has_section(path: Path, k: str, parent: bool) -> bool:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    for raw in text.splitlines():
        m = SECTION_RE.match(raw)
        if not m:
            continue
        if parent and m.group(1) is None:
            return True
        if not parent and m.group(1) == k:
            return True
    return False


def cmd_find(a: argparse.Namespace) -> int:
    cfg = config_for(a)
    path = Path(a.dir) / FINDINGS
    if not path.exists():
        path.write_text(cfg.findings_header, encoding="utf-8")
    parent = a.rung == "parent"
    k = str(a.attempt) if a.attempt is not None else ("P" if parent else "1")
    if parent and a.attempt is None:
        k = "P"
    ts = fmt(datetime.now(timezone.utc))
    chunk = ""
    if not has_section(path, k, parent):
        if parent:
            chunk += "\n## P verification\n\n"
        else:
            model = a.model or cfg.model_for(a.rung)
            chunk += "\n## A%s %s %s agent %s %s\n\n" % (k, a.rung, model, a.agent or "-", ts)
    fid = next_finding_id(path, k)
    entry = "- %s %s" % (fid, a.finding.strip())
    entry += " - evidence: %s" % (a.evidence.strip() if a.evidence else "none")
    if a.supersedes:
        entry += " - supersedes %s" % a.supersedes
    entry += " - %s\n" % a.status
    append_text(path, chunk + entry)
    print(fid)
    return 0


# ------------------------------------------------------------------------ attempts


def attempts_for(rows: "list[dict]", unit: str) -> "list[dict]":
    return [r for r in rows if r.get("unit") == unit]


def cmd_attempt(a: argparse.Namespace) -> int:
    cfg = config_for(a)
    d = Path(a.dir)
    rows = read_jsonl(d / ATTEMPTS)
    mine = attempts_for(rows, a.unit)
    k = a.k
    if k is None:
        if a.event == "open":
            k = max([int(r.get("k") or 0) for r in mine] or [0]) + 1
        else:
            opens = [r for r in mine if r.get("event") == "open"]
            closes = [r for r in mine if r.get("event") == "close"]
            if opens and len(opens) > len(closes):
                k = opens[-1].get("k")          # the attempt still in flight
            else:
                k = max([int(r.get("k") or 0) for r in mine] or [0]) + 1
    rung = a.rung
    if rung is None:
        same = [r for r in mine if r.get("k") == k and r.get("rung")]
        rung = same[-1]["rung"] if same else cfg.rungs[0]
    if a.event == "close" and not a.outcome:
        sys.exit("attempt close needs --outcome (%s)" % "|".join(cfg.outcomes))
    if a.outcome and a.outcome not in cfg.outcomes:
        sys.exit("unknown --outcome %r (this config allows %s)"
                 % (a.outcome, "|".join(cfg.outcomes)))
    fields = {
        "k": k,
        "rung": rung,
        "model": a.model or cfg.model_for(rung),
        "agent": a.agent or "",
        "event": a.event,
        "outcome": a.outcome or "",
        "unit": a.unit,
        "shape": a.shape or "",
        "work_from": list(a.work_from or []),
        "findings_from": a.findings_from or "",
    }
    line = append_jsonl(d / ATTEMPTS, fields)
    print("attempt #%d %s %s k=%s rung=%s unit=%s%s"
          % (line["n"], line["ts"], a.event, k, rung, a.unit,
             " outcome=%s" % a.outcome if a.outcome else ""))
    return 0


# ---------------------------------------------------------------------- the ladder


def unit_state(d: Path, cfg: "Config | None" = None) -> dict:
    """Per-unit work status, rungs tried with outcomes, and the next rung."""
    cfg = cfg or DEFAULTS
    d = Path(d)
    work = read_jsonl(d / WORK)
    attempts = read_jsonl(d / ATTEMPTS)
    units: "dict[str, dict]" = {}

    def slot(u: str) -> dict:
        return units.setdefault(u, {"unit": u, "done": False, "wrong": False,
                                    "outputs": [], "work_n": None, "corrects": None,
                                    "rungs": [], "last_outcome": None, "last_shape": "",
                                    "open": False})

    for r in sorted(work, key=lambda r: r.get("n") or 0):
        u = r.get("unit")
        if not u:
            continue
        s = slot(u)
        v = r.get("verified")
        if v == "wrong":
            s["wrong"] = True
            s["done"] = False
            s["work_n"] = r.get("n")
            s["corrects"] = r.get("corrects")
        elif v in ("self", "parent"):
            s["wrong"] = False
            s["done"] = True
            s["work_n"] = r.get("n")
            s["outputs"] = list(r.get("outputs") or [])

    for r in sorted(attempts, key=lambda r: r.get("n") or 0):
        u = r.get("unit")
        if not u:
            continue
        s = slot(u)
        if r.get("event") == "open":
            s["open"] = True
            s["last_rung_open"] = r.get("rung")
            continue
        s["open"] = False
        rung, outcome = r.get("rung"), r.get("outcome") or ""
        s["rungs"].append({"rung": rung, "outcome": outcome})
        s["last_outcome"] = outcome
        if r.get("shape"):
            s["last_shape"] = r["shape"]
        if outcome == "done":
            s["done"] = not s["wrong"]

    for s in units.values():
        s["next"] = next_rung(s, cfg)
    return units


def next_rung(s: dict, cfg: "Config | None" = None) -> str:
    """The next rung for one unit's state, by the ladder procedure."""
    cfg = cfg or DEFAULTS
    rungs = cfg.rungs
    if s.get("done") and not s.get("wrong"):
        return "done"
    closed = s.get("rungs") or []
    if not closed:
        # nothing has closed yet: an attempt in flight keeps its rung, else start at the top
        return s.get("last_rung_open") or rungs[0]
    last = closed[-1]
    outcome = last.get("outcome") or ""
    if outcome == "a":
        return "reshape-or-user"
    rung = last.get("rung")
    idx = rungs.index(rung) if rung in rungs else -1
    if outcome == "d":
        return rung if rung in rungs else rungs[0]      # class d resumes on the same model
    if outcome == "done":
        return "done"
    # anything in the climb set, or unrecognised: climb one rung
    idx += 1
    tried = {r.get("rung") for r in closed if (r.get("outcome") or "") != "d"}
    while idx < len(rungs) and rungs[idx] in tried:
        idx += 1                                        # no model retries the same unit
    return rungs[idx] if idx < len(rungs) else USER


def cmd_next(a: argparse.Namespace) -> int:
    cfg = config_for(a)
    units = unit_state(Path(a.dir), cfg)
    s = units.get(a.unit)
    print(next_rung(s, cfg) if s else cfg.rungs[0])
    return 0


# -------------------------------------------------------------------------- resume


def digest(d: Path, cfg: "Config | None" = None) -> dict:
    cfg = cfg or DEFAULTS
    d = Path(d)
    units = unit_state(d, cfg)
    findings = parse_findings(d / FINDINGS)
    done, wrong, rungs, nxt = [], [], {}, {}
    for u in sorted(units):
        s = units[u]
        rungs[u] = [{"rung": r["rung"], "outcome": r["outcome"]} for r in s["rungs"]]
        if s["done"] and not s["wrong"]:
            done.append({"unit": u, "outputs": s["outputs"], "work_n": s["work_n"]})
        else:
            nxt[u] = s["next"]
        if s["wrong"]:
            wrong.append({"unit": u, "work_n": s["work_n"], "corrects": s["corrects"],
                          "last_outcome": s["last_outcome"], "last_shape": s["last_shape"]})
    return {
        "dir": d.as_posix(),
        "units_done": done,
        "units_wrong": wrong,
        "rungs": rungs,
        "next": nxt,
        "findings": [{"id": f["id"], "status": f["status"], "supersedes": f["supersedes"]}
                     for f in findings],
        "counts": {"units_done": len(done), "units_wrong": len(wrong),
                   "findings": len(findings), "attempts": sum(len(v) for v in rungs.values())},
    }


def render_digest(g: dict) -> str:
    out = ["task %s" % g["dir"],
           "units done: %d  wrong: %d  findings: %d  attempts closed: %d"
           % (g["counts"]["units_done"], g["counts"]["units_wrong"],
              g["counts"]["findings"], g["counts"]["attempts"])]
    out.append("DONE (cite these, never regenerate them)")
    for u in g["units_done"] or []:
        out.append("- %s  work#%s  outputs: %s"
                   % (u["unit"], u["work_n"], ", ".join(u["outputs"]) or "none"))
    if not g["units_done"]:
        out.append("- none")
    out.append("WRONG (redo these)")
    for u in g["units_wrong"] or []:
        out.append("- %s  work#%s corrects#%s  last: %s  shape: %s"
                   % (u["unit"], u["work_n"], u["corrects"],
                      u["last_outcome"] or "-", u["last_shape"] or "-"))
    if not g["units_wrong"]:
        out.append("- none")
    out.append("RUNGS TRIED")
    for u in sorted(g["rungs"]):
        tried = g["rungs"][u]
        out.append("- %s: %s" % (u, ", ".join("%s:%s" % (r["rung"], r["outcome"] or "-")
                                              for r in tried) or "none"))
    if not g["rungs"]:
        out.append("- none")
    out.append("NEXT RUNG")
    for u in sorted(g["next"]):
        out.append("- %s: %s" % (u, g["next"][u]))
    if not g["next"]:
        out.append("- none open")
    out.append("FINDINGS (ids only; read findings.md for the text)")
    for f in g["findings"]:
        out.append("- %s %s%s" % (f["id"], f["status"],
                                  " supersedes %s" % f["supersedes"] if f["supersedes"] else ""))
    if not g["findings"]:
        out.append("- none")
    return "\n".join(out)


def cmd_resume(a: argparse.Namespace) -> int:
    cfg = config_for(a)
    d = Path(a.dir)
    if not d.is_dir():
        sys.exit("no such task dir: %s" % d)
    g = digest(d, cfg)
    print(json.dumps(g, indent=2) if a.json else render_digest(g))
    return 0


# --------------------------------------------------------------------------- index

INDEX_KEYS = ("ts", "session", "repo", "task_id", "subject", "task_dir", "status",
              "rungs", "final_rung", "work_units", "findings", "wrong", "events")


def index_line(g: dict, repo: str, task_id: str, subject: str, task_dir: str,
               session: "str | None" = None, events: "list[str] | None" = None,
               cfg: "Config | None" = None) -> dict:
    """The value-free index line, built from a `resume --json` digest."""
    rungs, final = [], None
    for u in sorted(g.get("rungs") or {}):
        for r in g["rungs"][u]:
            rungs.append("%s:%s" % (r.get("rung"), r.get("outcome") or "-"))
            final = r.get("rung")
    nxt = g.get("next") or {}
    if any(v in ("user", "reshape-or-user") for v in nxt.values()):
        status = "user"
    elif nxt:
        status = "open"
    else:
        status = "done"
    return {
        "session": session or env_session(cfg) or "unknown",
        "repo": repo,
        "task_id": task_id,
        "subject": subject,
        "task_dir": task_dir,
        "status": status,
        "rungs": rungs,
        "final_rung": final,
        "work_units": len(g.get("units_done") or []),
        "findings": len(g.get("findings") or []),
        "wrong": len(g.get("units_wrong") or []),
        "events": list(events or []),
    }


def cmd_index(a: argparse.Namespace) -> int:
    cfg = config_for(a)
    raw = sys.stdin.read() if a.from_json == "-" else Path(a.from_json).read_text(encoding="utf-8")
    try:
        g = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit("--from-json is not JSON: %s" % e)
    if not isinstance(g, dict):
        sys.exit("--from-json must be a `resume --json` object")
    fields = index_line(g, a.repo, a.task_id, a.subject, a.task_dir, a.session, a.events, cfg)
    line = append_jsonl(a.repo_ledger, fields, with_n=False)
    print("tasks %s %s %s %s" % (line["ts"], a.repo, a.task_id, line["status"]))
    return 0


# ------------------------------------------------------------------- config report


def cmd_config(a: argparse.Namespace) -> int:
    cfg = config_for(a)
    d = cfg.as_dict()
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    print("config: %s" % (d["config_path"] or "defaults (no file found)"))
    print("ladder: %s" % ", ".join("%s=%s" % (k, v) for k, v in cfg.ladder.items()))
    print("climb: %s" % ", ".join(cfg.climb))
    print("outcomes: %s" % ", ".join(cfg.outcomes))
    print("task_root: %s" % cfg.task_root)
    print("session_env: %s" % ", ".join(cfg.session_env))
    return 0


# ----------------------------------------------------------------------------- cli


def version() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=PROG,
                                 description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--version", action="store_true", help="print this file's sha256")
    ap.add_argument("--config", help="ratchet.toml or ratchet.json; else $%s, the cwd or a "
                                     "parent, then ~/.config/ratchet/" % CONFIG_ENV)
    sub = ap.add_subparsers(dest="cmd")

    def add(name, help):
        p = sub.add_parser(name, help=help)
        # the same flag after the subcommand; SUPPRESS keeps the outer value when absent
        p.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return p

    p = add("init", "create a task dir, or add the missing files to one")
    p.add_argument("--dir")
    p.add_argument("--repo", "--workflow", dest="repo")
    p.add_argument("--root", help="task root; default from the config (%s)" % DEFAULT_TASK_ROOT)
    p.add_argument("--task-id", dest="task_id")
    p.set_defaults(fn=cmd_init)

    p = add("work", "append a completed work unit")
    p.add_argument("dir")
    p.add_argument("--unit", required=True)
    p.add_argument("--attempt", type=int, required=True)
    p.add_argument("--rung", required=True)
    p.add_argument("--agent")
    p.add_argument("--phase")
    p.add_argument("--output", action="append", help="repeatable; paths under the task dir")
    p.add_argument("--method")
    p.add_argument("--verified", required=True, choices=list(VERIFIED))
    p.add_argument("--control")
    p.add_argument("--corrects", type=int)
    p.set_defaults(fn=cmd_work)

    p = add("find", "append a finding to findings.md")
    p.add_argument("dir")
    p.add_argument("--finding", required=True)
    p.add_argument("--rung", required=True, help="a ladder rung, or `parent` for verification")
    p.add_argument("--attempt")
    p.add_argument("--model")
    p.add_argument("--agent")
    p.add_argument("--evidence")
    p.add_argument("--status", default="established", choices=list(STATUSES))
    p.add_argument("--supersedes")
    p.set_defaults(fn=cmd_find)

    p = add("attempt", "open or close an attempt")
    p.add_argument("event", choices=["open", "close"])
    p.add_argument("dir")
    p.add_argument("--unit", required=True)
    p.add_argument("--k", type=int)
    p.add_argument("--rung")
    p.add_argument("--model")
    p.add_argument("--agent")
    p.add_argument("--outcome", help="done, a, d, or one of the config's climb outcomes")
    p.add_argument("--shape")
    p.add_argument("--work-from", action="append", dest="work_from")
    p.add_argument("--findings-from", dest="findings_from")
    p.set_defaults(fn=cmd_attempt)

    p = add("resume", "value-free digest of a task dir")
    p.add_argument("dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_resume)

    p = add("next", "the next ladder rung for one unit")
    p.add_argument("dir")
    p.add_argument("--unit", required=True)
    p.set_defaults(fn=cmd_next)

    p = add("index", "append the workflow's value-free tasks.jsonl line")
    p.add_argument("--from-json", dest="from_json", required=True, help="a resume --json file, or -")
    p.add_argument("--repo-ledger", dest="repo_ledger", required=True)
    p.add_argument("--repo", "--workflow", dest="repo", required=True)
    p.add_argument("--task-id", dest="task_id", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--task-dir", dest="task_dir", required=True, help="the task dir")
    p.add_argument("--session")
    p.add_argument("--events", action="append")
    p.set_defaults(fn=cmd_index)

    p = add("config", "print the effective config and where it came from")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_config)
    return ap


def main(argv: "list[str]") -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.version:
        print(version())
        return 0
    if not getattr(a, "cmd", None):
        ap.print_help()
        return 2
    try:
        return a.fn(a)
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
