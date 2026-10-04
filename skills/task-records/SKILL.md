---
name: task-records
description: How a parent session and its delegated agents keep a task's records - completed work units in work.jsonl, findings written as they are learned in findings.md, one open and one close line per attempt in attempts.jsonl, and a value-free index line per task - so a retry or a fresh session starts from what earlier attempts produced. Use when dispatching, resuming or closing any delegated agent task, and when writing or reading those records with ratchet.py. Prevents retries that redo finished work, findings that exist only in a hand-back nobody kept, and task state that dies with the session.
---

# Task records

Status: provisional (0 sources cited, 0 cases)

Reference files, read when the situation arises:
- `formats.md` — the task dir layout and every record's fields.
- `../model-ladder/SKILL.md` — classifying a failure and choosing the next rung.
- `tools/ratchet.py --help` — the helper that writes and reads the records.

## The three records

A task is one unit of delegated work with its own dir outside git (`formats.md`). Beside any
step log the workflow already keeps:

| file | writer | holds |
|---|---|---|
| `work.jsonl` | the agent; the parent appends verdicts | each completed, reusable unit: outputs, method, whether it was verified |
| `findings.md` | the agent, as it learns; the parent in `## P verification` | findings with evidence and status, one section per attempt, append-only |
| `attempts.jsonl` | the parent | one open and one close line per attempt: rung, model, agent id, outcome, shape |

The workflow's own `ledger/tasks.jsonl` gets one value-free index line per task, so a fresh
session finds the task without opening the task dir.

## Rules

1. **Write as you go.** A finding goes into `findings.md` when it is learned, and a unit goes
   into `work.jsonl` when it is finished, not at the end of the run. An agent that is stopped
   mid-run leaves behind everything it had established.
2. **A retry starts from the records.** Run `ratchet.py resume <dir>`, read `findings.md`
   and `work.jsonl`, then work only on the failing unit and any unit marked `wrong`. Verified
   units are cited, never regenerated.
3. **Failures climb the ladder** (`../model-ladder/SKILL.md`). Only the failing unit climbs;
   each new unit starts at the top rung.
4. **The parent verifies.** A result counts as `parent` verified only after a control the
   parent ran: an independent recomputation, a structural check, or the artifact's own
   validator. A failed check is recorded as class w, with a `wrong` work line naming the line
   it corrects.
5. **Standing rules come from the adapter.** What may appear in a finding, where bulky or
   sensitive content must stay, and what every dispatch prompt must say are the consuming
   workflow's rules, written in its adapter skill — not here.

## One attempt, end to end

```
ratchet.py init --dir <task_dir>                      # or --repo <name>
ratchet.py attempt open <dir> --k 1 --rung <rung> --unit <u> --agent <id8>
# the agent, as it works:
ratchet.py find <dir> --attempt 1 --rung <rung> --agent <id8> \
    --finding "<what was learned>" --evidence "<path|work#n>" --status established
ratchet.py work <dir> --unit <u> --attempt 1 --rung <rung> --agent <id8> \
    --phase <p> --output out/<file> --method "<how>" --verified self --control "<control>"
# the parent, when the agent returns:
ratchet.py work <dir> --unit <u> --attempt 1 --rung parent --verified parent \
    --method "<the control the parent ran>" --control "<its output>"
ratchet.py find <dir> --rung parent --finding "<verdict>" --evidence "<control path>"
ratchet.py attempt close <dir> --k 1 --rung <rung> --unit <u> --outcome done
# at task end:
ratchet.py resume <dir> --json > resume.json
ratchet.py index --from-json resume.json --repo-ledger ledger/tasks.jsonl \
    --repo <name> --task-id <id> --subject "<subject>" --task-dir <dir>
```

`resume` prints a value-free digest: units done with their outputs, units marked wrong, rungs
tried with outcomes, the next rung per open unit, and finding ids with statuses — never
finding text. It is the first thing every retry reads.

## Reading a digest

- **DONE** — cite by work line number; do not regenerate.
- **WRONG** — redo; not an input to anything else.
- **RUNGS TRIED** — with the shape of each failure, so a retry does not repeat it.
- **NEXT RUNG** — what to dispatch; `user` or `reshape-or-user` means hand it over.
