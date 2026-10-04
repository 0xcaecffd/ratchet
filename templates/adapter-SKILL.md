---
name: <project>-ledger-adapter
description: <Project>'s adapter to ratchet — where the config and task dirs live, the verification controls this project accepts, what may be written inline in a finding, and the standing rules every dispatch repeats. Use before dispatching, resuming or closing any <project> task. Prevents task dirs in the wrong place, work claimed as verified without a control, and dispatches that leave out a standing rule.
---

# <Project> adapter to ratchet

Status: provisional (0 sources cited, 0 cases)

The generic method is in `task-records`, `model-ladder` and `skill-learning`. This file holds
only what is true of this project. Copy it into your project's skills dir, rename the
directory to match `name`, and fill in every section. Delete nothing: a section with nothing
in it is a decision not yet made.

## 1. Where things live

| thing | value |
|---|---|
| config | `<path to ratchet.toml>` (`ratchet.py config` must print this path) |
| tool | `<path to ratchet.py>` |
| task root | `<root>/<workflow>/tasks/t<YYYYMMDD>-<n>/` |
| task index | `<repo>/ledger/tasks.jsonl` |
| agents dir | `<where gen_agents.py writes the rung cells>` |
| hypotheses | `<repo>/hypotheses.jsonl` |

What a `task_id` and a `subject` are in this project: `<e.g. a dataset id, a ticket, a build>`.

## 2. Verification controls

A unit is `verified parent` only after one of these, run by the parent:

- `<control 1 — e.g. recompute the output with an independent tool and compare a digest>`
- `<control 2 — e.g. the artifact's own validator / schema check / test suite>`
- `<control 3 — e.g. a count reproduced from a different source>`

A failed control is class `w`: write the `wrong` work line naming the line it corrects, add a
`## P verification` finding, then climb.

What counts as "could have failed": `<how this project avoids controls that always pass>`.

## 3. Content rules for findings and replies

- Allowed inline in `findings.md`: `<counts, ids, offsets, …>`.
- Must stay in files under `out/`, cited by path: `<bulk data, raw records, anything sensitive>`.
- Never written anywhere in the records: `<credentials, tokens, personal data, …>`.
- The reply to the parent carries: `<counts, ids, paths>`.

Keep these aligned with `[findings] note` in the config, which is what a new `findings.md`
header tells every agent.

## 4. Standing rules for every dispatch

Repeated in every prompt, including retries (see `model-ladder/retry-template.md`):

1. `<purpose: the field or artifact this work feeds>`
2. `<environment rule: where work runs, what it may not touch>`
3. `<approval rule: what is never done without the user saying so — commits, pushes, writes
   outside the task dir, external calls>`
4. `<reply rule: what the hand-back must contain>`

## 5. Ladder specifics

- Rungs: as in the config; `ratchet.py config` is the source of truth.
- Cells: `<name>-<rung>-<effort>`, regenerated with
  `python templates/gen_agents.py --base <base>.md --name <name> --agents-dir <dir>`.
- Project-specific failure classes, if any, and what each means: `<…>`.
- When to hand to the user beyond the generic rules: `<…>`.

## 6. Learn step

- Self-test to run after a skill edit: `<python skills/skill-learning/skillcheck.py skills
  --strict, or the project's own>`.
- Where a lesson's source id comes from: `<…>`.
- Which skills are preloaded for this project's runs: `<…>`.
