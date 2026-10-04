# task-records: formats

## Task dirs

A task dir holds one task's records and outputs. It lives wherever the workflow's config
points (`tasks.root` in `ratchet.toml`), in scratch space, and never in git.

```
<root>/<workflow>/tasks/t<YYYYMMDD>-<n>/
    ledger.jsonl    the workflow's own per-action log, if it keeps one
    work.jsonl      completed reusable units
    findings.md     append-only findings, one section per attempt
    attempts.jsonl  one open and one close line per attempt
    out/            outputs; findings cite them by path
```

Create it with `ratchet.py init --repo <workflow>` (prints the path), or
`init --dir <dir>` to add the missing files to a dir that already exists. Every jsonl line
gets `n` and `ts` from the tool; a caller may not set either.

## work.jsonl

One line per completed, reusable unit:

```
{"n", "ts", "attempt": k, "rung": "opus5", "agent": "<id8>", "unit": "u2-extract",
 "phase": "extract", "outputs": ["out/u2/table.csv"], "method": "scripts/extract.py",
 "verified": "self|parent|no|wrong", "control": "<path or log id>", "corrects": n}
```

- `verified: self` means the agent ran the control itself. `parent` and `wrong` lines are
  written by the parent (`--rung parent`), and a `wrong` line must name the line it corrects.
- A unit's state is its latest line. `self` or `parent` means done; `wrong` means it must be
  redone and is not an input.

## findings.md

Append-only, one section per attempt, plus the parent's own section:

```
## A2 opus5 claude-opus-5 agent a640d72d 2026-10-03T05:12:40Z
- F2.1 <finding> - evidence: out/u2/controls.json - established
- F2.2 <finding> - evidence: work#4 - supersedes F1.3 - open
## P verification
- FP.1 <verdict> - evidence: <control path> - established
```

- Write entries with `ratchet.py find`, which assigns the id and opens the section. The
  separators are ASCII ` - `, the status always comes last, and missing evidence is written
  `evidence: none`.
- Write each finding when it is learned, not at the end.
- A correction is a new entry naming the entry it supersedes. Earlier lines are never edited.
- What may appear inline is the adapter's rule. The default header says: counts, offsets, ids
  and paths only, with bulky or sensitive content in `out/`, cited by path.
- `resume` reports finding ids and statuses only, never the text.

## attempts.jsonl

Written by the parent. Two lines per attempt:

```
{"n", "ts", "k": 2, "rung": "opus5", "model": "claude-opus-5", "agent": "<id>",
 "event": "open", "unit": "u2-extract", "work_from": [3, 4], "findings_from": "A1"}
{"n", "ts", "k": 2, "rung": "opus5", "event": "close", "unit": "u2-extract",
 "outcome": "done|b|b?|c|w|a|d|unavailable", "shape": "<short, value-free>"}
```

`outcome` must be `done`, `a`, `d`, or one of the config's climb outcomes; the tool refuses
anything else. `shape` is what a retry must not repeat.

## <workflow>/ledger/tasks.jsonl

One value-free line per task (no `n`), written by the parent at the end of the task. The
attempt line holds the real dispatch agent id; a worker's own records may use its own label.
`status` is `user` if an unfinished unit's next rung is `user` or `reshape-or-user`, `open` if
a unit is unfinished, else `done`.

```
{"ts", "session", "repo", "task_id", "subject", "task_dir",
 "status": "open|done|user", "rungs": ["opus55:b", "opus5:done"],
 "final_rung": "opus5", "work_units": 6, "findings": 14, "wrong": 1, "events": ["events#97"]}
```

On Windows, running `index` from Git Bash with a POSIX task dir needs `MSYS_NO_PATHCONV=1`,
or the path is rewritten to a Windows one.

## session ids

`session` comes from the first set variable in the config's `session.env` list (default
`RATCHET_SESSION`), or `--session`, else `unknown`.
