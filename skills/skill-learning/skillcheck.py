#!/usr/bin/env python3
"""Structural check for a directory of Claude Code skills (ratchet convention).

    python skillcheck.py <skills_dir> [--strict]

Per skill directory:
- SKILL.md frontmatter has `name` and `description`, and `name` equals the directory name.
- LEARNED.md and cases.jsonl exist; cases.jsonl lines are JSON objects with id, kind,
  input, expect; ids unique. An empty cases.jsonl is a warning unless the `Status:` line
  declares `0 cases` (a skill that has not yet been measured says so instead of pretending).
- The `Status:` line's "N sources cited, M cases" match: N = distinct 12+ hex ids or
  `<group>/<id>`-style source ids in the second column of LEARNED.md, M = case rows.
- Relative `*.md` references (backticked or Markdown links, optional #anchor) resolve,
  and anchors match a heading (task-dir `out/` paths are skipped; repo-root paths resolve).

Errors exit 1; warnings only with --strict. Stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BACKTICK = re.compile(r"`([^`\n]+)`")
MDLINK = re.compile(r"\]\(([^)\s]+)\)")
STATUS = re.compile(r"^Status:.*$", re.M)
CASE_FIELDS = ("id", "kind", "input", "expect")


def frontmatter(text: str) -> dict[str, str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    out = {}
    for line in text[4:end].splitlines():
        if line and line[0] not in " \t-":
            k, sep, v = line.partition(":")
            if sep:
                out[k.strip()] = v.strip()
    return out


def slug(h: str) -> str:
    return re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")


def anchors(p: Path) -> set[str]:
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*)$", p.read_text(encoding="utf-8"), re.M)}


HEX12 = re.compile(r"\b[0-9a-f]{12,64}\b")


def sources(learned: str) -> int:
    """Distinct hash-like ids (12+ hex, first 12 kept) plus distinct non-hex source ids
    (dataset, ticket or rule ids) from LEARNED.md's second column."""
    ids = {h[:12] for h in HEX12.findall(learned) if len(set(h)) > 2}  # not padding runs like 000000000000
    for line in learned.splitlines():
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 2 and cols[1] and cols[1].lower() not in ("none", "-") and not HEX12.search(cols[1]):
            ids.add(cols[1])
    return len(ids)


def check(skill: Path) -> tuple[list[str], list[str]]:
    err, warn = [], []
    sm = skill / "SKILL.md"
    if not sm.is_file():
        return [f"{skill.name}: no SKILL.md"], warn
    text = sm.read_text(encoding="utf-8")
    fm = frontmatter(text)
    if fm is None or "name" not in fm or "description" not in fm:
        err.append(f"{skill.name}: frontmatter needs name and description")
    elif fm["name"] != skill.name:
        err.append(f"{skill.name}: name `{fm['name']}` != directory")
    learned = skill / "LEARNED.md"
    cases = skill / "cases.jsonl"
    if not learned.is_file():
        err.append(f"{skill.name}: no LEARNED.md")
    if not cases.is_file():
        err.append(f"{skill.name}: no cases.jsonl")
    m = STATUS.search(text)
    declares_none = bool(m and re.search(r"\b0\s+cases?\b", m.group(0)))
    n_cases = 0
    if cases.is_file():
        seen = set()
        for i, line in enumerate(cases.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                err.append(f"{skill.name}: cases.jsonl:{i} bad JSON ({e.msg})")
                continue
            missing = [f for f in CASE_FIELDS if f not in row]
            if missing:
                err.append(f"{skill.name}: cases.jsonl:{i} missing {missing}")
            if row.get("id") in seen:
                err.append(f"{skill.name}: cases.jsonl:{i} duplicate id {row.get('id')}")
            seen.add(row.get("id"))
            n_cases += 1
        if n_cases == 0 and not declares_none:
            warn.append(f"{skill.name}: cases.jsonl is empty")
    if not m:
        warn.append(f"{skill.name}: no Status: line")
    elif learned.is_file():
        computed = {"source": sources(learned.read_text(encoding="utf-8")), "case": n_cases}
        for label, rx in (("source", r"(\d+)\s+sources?\b"), ("case", r"(\d+)\s+cases?\b")):
            n = re.search(rx, m.group(0))
            if n and int(n.group(1)) != computed[label]:
                warn.append(f"{skill.name}: stale Status: says {n.group(1)} {label}s, computed {computed[label]}")
    for md in skill.rglob("*.md"):
        if md.name == "LEARNED.md":
            continue
        body = md.read_text(encoding="utf-8")
        for ref in BACKTICK.findall(body) + MDLINK.findall(body):
            target, _, anchor = ref.strip().partition("#")
            if not target.endswith(".md") or any(c in target for c in "<>* ~") or target.startswith(("/", "http", "out/")):
                continue
            # the skills dir and its ancestors, so repo-root-relative refs also resolve
            bases = (md.parent, skill, skill.parent, skill.parent.parent, skill.parent.parent.parent)
            for base in bases:
                p = base / target
                if p.is_file():
                    if anchor and slug(anchor) not in anchors(p):
                        err.append(f"{skill.name}: {md.name}: anchor `{ref}` not found")
                    break
            else:
                # A bare name without an anchor is often a run-dir or generic file name
                # (STATE.md, report.md, learn.md); only paths and anchored refs must resolve.
                if "/" in target or anchor:
                    err.append(f"{skill.name}: {md.name}: `{ref}` does not resolve")
    return err, warn


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    root, strict = Path(argv[0]), "--strict" in argv
    errors = warnings = 0
    for skill in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if not (skill / "SKILL.md").is_file():
            continue  # not a skill (e.g. ~/.claude/skills/synced)
        e, w = check(skill)
        errors += len(e)
        warnings += len(w)
        print(f"{skill.name:<28} {'ok' if not e else 'ERROR'}{'' if not w else '  (' + str(len(w)) + ' warning)'}")
        for line in e + w:
            print("   ", line)
    print(f"skillcheck: {errors} errors, {warnings} warnings")
    return 1 if errors or (strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
