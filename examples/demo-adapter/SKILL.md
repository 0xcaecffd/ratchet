---
name: demo-adapter
description: Adapter for the demo release-notes pipeline — where its config and task dirs live, the controls that make a unit verified, what may be written inline in a finding, and the standing rules every dispatch repeats. Use before dispatching, resuming or closing a release-notes task. Shows what a filled-in ratchet adapter looks like.
---

# Demo adapter: the release-notes pipeline

Status: provisional (0 sources cited, 0 cases)

A worked, non-sensitive example of `templates/adapter-SKILL.md`. The workflow: each night a
CSV export of merged pull requests becomes a release-notes page. Three units per task:

| unit | does |
|---|---|
| `u1-export` | read `prs.csv`, normalise columns, write `out/u1/prs.normalised.csv` |
| `u2-group` | group rows into sections (feature, fix, docs), write `out/u2/sections.json` |
| `u3-draft` | draft `out/u3/release-notes.md` from the sections |

The generic method is in `task-records`, `model-ladder` and `skill-learning`.

## 1. Where things live

| thing | value |
|---|---|
| config | `examples/demo-adapter/ratchet.toml` |
| tool | `tools/ratchet.py` |
| task root | `~/demo-pipeline/scratch/release-notes/tasks/t<YYYYMMDD>-<n>/` |
| task index | `demo-pipeline/ledger/tasks.jsonl` |
| agents dir | `.claude/agents/` (cells `notes-strong-medium`, `notes-cheap-medium`) |
| hypotheses | `demo-pipeline/hypotheses.jsonl` |

`task_id` is `t<YYYYMMDD>-<n>`; `subject` is the release tag the notes are for (`v2.14.0`).
`session` comes from `$DEMO_RUN_ID` (the nightly job sets it) or `$CI_JOB_ID`.

## 2. Verification controls

A unit is `verified parent` only after one of these, run by the parent:

- `u1-export`: row count of the normalised CSV equals the input's non-empty data rows, and
  every required column is present (`wc -l` plus a header check — not the agent's own report).
- `u2-group`: every PR number in the input appears exactly once across the sections, and no
  section holds a PR number that was not in the input.
- `u3-draft`: every PR number in `sections.json` appears in the draft, and every link in the
  draft resolves to a PR number in the input (no invented references).

A failed control is `rejected`: write the `wrong` work line naming the line it corrects, add a
`## P verification` finding, then climb. "Could have failed" means the control is computed
from the input, never from the agent's own output alone.

## 3. Content rules for findings and replies

- Allowed inline in `findings.md`: row counts, column names, PR numbers, section names, paths.
- Must stay in files under `out/`: exported rows, drafted prose, anything quoted from a PR.
- Never written anywhere in the records: repository tokens, reviewer email addresses.
- The reply to the parent carries counts, PR numbers, unit names and paths.

## 4. Standing rules for every dispatch

1. Purpose: this unit feeds the release-notes page for `<tag>`; `u3-draft` is what ships.
2. Environment: work only inside the task dir; the input CSV is read-only; no network calls.
3. Approval: never commit, push, or publish the page. The parent does that after the user
   reads the draft.
4. Reply: the work lines added, the finding ids with statuses, what is still open, and any
   denied call or stopped response with its class and shape.

## 5. Ladder specifics

- Rungs: `strong` (claude-opus-5), then `cheap` (claude-sonnet-5), then the user.
- Cells: regenerate with
  `python templates/gen_agents.py --base templates/worker.md --name notes
  --config examples/demo-adapter/ratchet.toml --agents-dir .claude/agents`.
- Classes: `stopped`, `rejected`, `blocked` climb; `a` (denied call) and `d` (interruption)
  behave as in `model-ladder`.
- Hand to the user when `cheap` fails, or when `u3-draft` is rejected twice for the same
  shape — that usually means the sections are wrong, not the drafting.

## 6. Learn step

- Self-test after a skill edit: `python skills/skill-learning/skillcheck.py skills --strict`.
- A lesson's source id is the release tag plus the task id (`v2.14.0/t20261004-1`).
- Preloaded for every run: this adapter and `task-records`.

## A task, start to finish

```
export DEMO_RUN_ID=nightly-2026-10-04
RATCHET_CONFIG=examples/demo-adapter/ratchet.toml
dir=$(python tools/ratchet.py init --repo release-notes)

python tools/ratchet.py attempt open  "$dir" --k 1 --rung strong --unit u1-export --agent 1a2b3c4d
# ... the agent writes findings and one work line, verified self ...
python tools/ratchet.py work "$dir" --unit u1-export --attempt 1 --rung parent \
    --verified parent --method "wc -l + header check against prs.csv" --control out/u1/rowcount.txt
python tools/ratchet.py attempt close "$dir" --k 1 --rung strong --unit u1-export --outcome done

python tools/ratchet.py attempt close "$dir" --k 1 --rung strong --unit u2-group \
    --outcome rejected --shape "two PR numbers in both fix and docs"
python tools/ratchet.py next "$dir" --unit u2-group        # -> cheap
# ... retry on the cheap rung, citing u1-export's work line ...

python tools/ratchet.py resume "$dir" --json > "$dir/resume.json"
python tools/ratchet.py index --from-json "$dir/resume.json" \
    --repo-ledger demo-pipeline/ledger/tasks.jsonl --repo release-notes \
    --task-id t20261004-1 --subject v2.14.0 --task-dir "$dir"
```
