---
name: model-ladder
description: How a parent session retries a failed delegated agent down an ordered model ladder - classifying the failure (a denied call, a stopped response, an unfinished return, an environment error, a rejected result, a session interruption), moving only the failing unit one rung down, reusing the records earlier attempts left, and knowing when to stop and hand the task to the user. Use when an agent is stopped, returns unfinished, errors, or produces a result the parent's check rejects, and when choosing which model to dispatch a task unit to. Prevents ad hoc model choices nobody can measure, retries that repeat the shape that just failed, and loops that never reach a human.
---

# The model ladder

Status: provisional (0 sources cited, 0 cases)

The ladder is config, not code: `ratchet.py config` prints the rungs in order, and
`ratchet.py next <dir> --unit <u>` applies the procedure below to a task's records. See
`../task-records/SKILL.md` for the records themselves and `retry-template.md` for the dispatch
prompt.

## Rungs

A rung is a label mapped to a model id, strongest first, ending at `user`. The default:

| # | rung | model id |
|---|---|---|
| 1 | opus55 | claude-opus-5-5 |
| 2 | opus5 | claude-opus-5 |
| 3 | opus48 | claude-opus-4-8 |
| 4 | opus46 | claude-opus-4-6 |
| 5 | sonnet55 | claude-sonnet-5-5 |
| 6 | sonnet5 | claude-sonnet-5 |
| 7 | sonnet46 | claude-sonnet-4-6 |
| 8 | user | — |

All rungs run at one effort level; a project that measures effort as a factor adds it to the
rung label (`opus5-high`) rather than guessing per attempt. Confirm the model an attempt
actually ran on from its transcript and record it in the attempt line. A rung whose model id
is rejected by the API is closed `unavailable` and skipped.

## Classify, then decide

| class | what happened | next |
|---|---|---|
| b | the response was stopped by a safety classifier | next rung |
| b? | the agent returned without finishing, with no tool error | next rung |
| w | the parent's check rejected the result (recomputation mismatch, failed structural control, the artifact's own validator) | next rung, after recording the verdict |
| c | a command failed for an environment reason | the agent fixes it in place first; if that fix fails, next rung |
| a | a tool call was denied by permissions | reshape the call, or hand to the user; no model change |
| d | session interruption (login, user stop, API error) | resume the same rung |
| done | the unit is finished and verified | close the unit |

Which classes climb is the config's `outcomes.climb` (default `b`, `b?`, `c`, `w`,
`unavailable`). A project with different failure kinds renames them there, and the tool then
refuses any outcome outside its own set.

Rules:
**Permission failures never trigger the ladder** (user, 2026-10-05). A denied tool call (class a)
is a configuration wall, not a model problem: every rung meets the same wall, so climbing only
spends money (one measured run spent $38 across 12 calls this way). Stop the unit at the rung it
was on, record the denied tools, and hand to the user, who changes the allowlist or the prompt.
A headless driver stops the whole stage on class a, not only the unit.

- Only the failing unit climbs. Each new unit starts at rung 1.
- **The parent's own stopped responses climb too.** When the parent session's response is
  stopped mid-task (class b on writing an answer, a file or code that is not yet a delegated
  unit), the parent treats the interrupted work as an implicit unit: it records the stop, then
  dispatches that work, with a short self-contained prompt, to the next rung instead of only
  reporting it. Nothing in the harness does this automatically; it is the parent's step.
- A model never gets a second attempt at the same unit, except to resume after class d.
- A retry must not reuse the failed shape unchanged (the `shape` in the closing attempt line).
- Hand to the user when the last rung fails, when class a needs a permission, or when two
  rungs fail with the same shape for a reason that is not the model (for example, an input the
  task cannot reach). Say which it is.

## Parent steps for one attempt

1. `ratchet.py attempt open <dir> --k K --rung R --unit U --model <id> --agent <id8>`
   once the agent id is known.
2. When the agent returns, verify each new `work.jsonl` unit it claims. Append
   `ratchet.py work … --rung parent --verified parent|wrong [--corrects N]
   --control <path>`, and add a `## P verification` finding.
3. `ratchet.py attempt close <dir> --k K --rung R --unit U --outcome <class>
   --shape "<what was stopped or wrong>"`, plus whatever event log the workflow keeps.
4. Ask `ratchet.py next <dir> --unit U` for the next rung, then dispatch with
   `retry-template.md`, or close the task.
5. At task end: `resume --json` → `index --repo-ledger <workflow>/ledger/tasks.jsonl`.

## Metric

Per unit: the rung it completed on (or `user`), the class of each failed attempt, and whether
the parent's check passed. `resume --json` gives these, and the index line keeps the `rungs`
list — so the ladder's cost and benefit can be measured over many tasks instead of argued.
