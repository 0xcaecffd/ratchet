---
name: skill-learning
description: How a project turns finished work into skills - the learn step that ends every run; measured lessons into skills with LEARNED.md and cases.jsonl, unmeasured ones into hypotheses.jsonl with confirm and falsify conditions written up front; when a lesson becomes a new skill; and the structural self-test skillcheck.py. Use at the end of any delegated-agent run, when creating or editing a skill, or when setting up the learning loop in a project that lacks it. Prevents skills that grow from opinion instead of measurement, lessons nobody can trace to the work that taught them, and hypotheses quietly treated as fact.
---

# Learning into skills

Status: provisional (0 sources cited, 0 cases)

Each project keeps its own skills and its own evidence; this skill is the shared method.
Project-specific facts (where run ids come from, which self-test to run, which skills are
preloaded) live in the project's adapter skill, never here.

## The rule

**Nothing goes into a skill that the work did not teach.** Every lesson cites what taught it:
the subject id the project uses (a hash, dataset id, ticket id, rule id), the date, and the
run's log or ledger id. A lesson nobody can trace is deleted, not softened.

## The learn step (ends every run)

A run's own records — completed work, findings, attempts, the model ladder — follow the sibling
skills `../task-records/SKILL.md` and `../model-ladder/SKILL.md`; the learn step reads the
run's `findings.md` as its log.

A run is not finished until this is done. A run that taught nothing says so in its log.

1. **What surprised us?** Anything a skill predicted wrongly, didn't cover, or that cost time;
   which loaded skills were wrong or unhelpful (the negative signal pruning needs); any helper
   script written twice (a tool candidate).
2. **Is it measured?** A measurement (a count, a funnel, an ablation, a reproduced control)
   goes into a skill. Anything else goes to the project's `hypotheses.jsonl` (format below).
   Update every hypothesis the run tested.
3. **Is it independent?** Confirmation needs a different subject — a different input, build or
   batch — not a second copy of the same one.
4. **Where does it belong?**
   - Sharpens a skill: edit the procedure at the point where the mistake would be made; the
     dated figure goes in that skill's evidence section or file.
   - Contradicts a skill: fix the rule and keep the old measurement visible ("previously X;
     `<id>` showed Z"); record the retraction in `LEARNED.md`.
   - A different method (its own trigger and steps): a new skill (below).
   - Belongs to another project's skill: never edit it; write an `outbound` entry naming that
     project, skill and section, for its next session.
5. **Record it.** Each changed skill gets a `LEARNED.md` line and, where it yields a known
   answer, a `cases.jsonl` row; update its `Status:` line; run the self-test. Commit skill
   edits separately from code. A promotion from hypothesis to skill is its own commit citing
   the hypothesis id and the confirming ids.

Blind or scored runs: skills are frozen. Write proposals to the run's own `learn.md` and let
the parent apply them after scoring.

## Files every skill carries

| file | what | rule |
|---|---|---|
| `SKILL.md` | frontmatter `name` (= directory) and `description` only; a `Status:` line; the procedure | lean; examples and figures go in reference files |
| `LEARNED.md` | one line per change: `date \| source id \| run/log id \| +added/~changed/-retracted \| section: what` | append-only history |
| `cases.jsonl` | known answers: `{"id","kind","input","expect"}` (+ `source`) | ids unique; an empty file is unhealthy once the skill has sources |
| evidence (section or `evidence.md`) | dated measurements behind each rule | superseded figures stay, marked |

`Status: provisional|established (N sources cited, M cases)`, with N = distinct source ids in
`LEARNED.md` and M = rows in `cases.jsonl`. Established needs independent confirmation of the
skill's main rules. A brand-new skill says `provisional (0 sources cited, 0 cases)` — honest,
and the self-test accepts it.

## hypotheses.jsonl (one per project)

`{"id":"H-0001","claim","raised_by","raised_on","target_skill","evidence","confirm_if","falsify_if","status","outbound"?}`

- `confirm_if` / `falsify_if` are written **when raised**, never after the result.
- `status`: open | confirmed | falsified | superseded. Evidence is appended, never rewritten.
- Ids are allocated by reading the file's maximum id immediately before writing. Parallel
  sessions collide: re-read and renumber on conflict, recording `renumbered_from`.
- A hypothesis is never cited as established. A lead may cite it by id.

## New skills

Create one only for a method with its own trigger and steps, taught by at least one run. It
starts `provisional` with a `LEARNED.md` whose first line says what created it, and it is
linked from the skill that routes to it. Method skills stay portable: no hosts, paths,
credentials or run ids outside `LEARNED.md`; those belong in the project's adapter skill.

## Self-test

Run after every skill edit:

```
python skills/skill-learning/skillcheck.py <skills_dir> [--strict]
```

It checks frontmatter, `LEARNED.md`/`cases.jsonl` presence and syntax, unique case ids, the
`Status:` counts, and that relative `*.md` references resolve. Errors exit 1; `--strict` also
fails on warnings. A project with its own richer self-test runs that instead and says so in
its adapter.

## Parallel sessions

Several sessions may edit the same skills at once. Before committing: re-read the files, check
`hypotheses.jsonl` for duplicate ids, run the self-test, and commit only your own hunks if
others' edits are present. A session started before another's edit sees the old skill: re-read
a skill before relying on a rule another run may have just changed.
