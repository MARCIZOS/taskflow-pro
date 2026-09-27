# Testing

## How to run

```bash
cd backend
pip install -r requirements-dev.txt
pytest                                   # SQLite in memory, about 3 seconds

# the same API/AI/concurrency tests against PostgreSQL
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/taskflow_test pytest

python -m scripts.benchmark              # engine timings, 1,000 tasks / 3,000 links

cd ../frontend
npm test                                 # Vitest: UI helper functions
npm run typecheck
```

## Test suite

| File | Tests | What it covers |
|---|---|---|
| `test_engine_cycles.py` | 11 | self link, duplicate, unknown task, 2- and 3-node loops (the A → B → C → A case), loop path reported in order, dry run leaves graph untouched, 2,000-deep chain (no recursion limit), diamonds and shortcuts are *not* loops, deterministic topological order |
| `test_engine_schedule.py` | 11 | diamond +3 → D +3 (not +6), uneven branches move D only by the real impact, 10 converging paths move once, multi-level propagation keeps durations, slack absorbs delays, never pulls earlier, unrelated tasks untouched, input not mutated, constraint violations, critical path with slack |
| `test_engine_status.py` | 6 | Ready without prerequisites, Blocked until all Done, blocked task cannot start, Backlog always allowed, rollback on regression re-blocks dependents |
| `test_engine_properties.py` | 2 × 300 examples | random DAGs (Hypothesis): after any delay every dependency holds, no task moves further than the delay, only descendants move, durations kept, idempotent, minimal; cycle check agrees with reachability and every reported loop is real |
| `test_api.py` | 18 | the four rules end to end through HTTP, dry-run endpoint, stale `version` rejected, reordering persists, adding a link pushes the successor, delete unblocks dependents, input validation, critical path on seed, state survives a fresh GET, demo reset |
| `test_ai.py` | 12 | each of the five gates drops exactly the bad suggestion, nothing saved before accept, accept goes through the engine (stale case), reject + precision metric, keyword model finds the missing seed link, LLM failure falls back, JSON parsing, OpenAI request shape (mocked), HTTP errors, rate limit |
| `test_concurrency.py` | 1 | two threads add A → B and B → A at the same moment: exactly one succeeds, the other gets `cycle` |
| `frontend/src/lib.test.ts` | 6 | grouping, optimistic move across and within columns, blocker gating, date formatting without time-zone drift, graph layering |

All backend tests pass on SQLite and PostgreSQL 16.

## Manual end-to-end checks (browser)

Run with the demo data (`python -m app.seed --reset`):

| Step | Expected | Result |
|---|---|---|
| Drag *Build backend API* to In Progress | refused, toast names the two blockers, card snaps back | pass |
| Drag *Build frontend UI* to In Progress, refresh page | stays in In Progress | pass |
| Extend *Gather requirements* end date by 3 days | 9 tasks move, *Build backend API* +3 | pass |
| Pick *Production release* as a prerequisite of *Gather requirements* | loop shown before Add is enabled | pass |
| Schema and auth to Done, API to In Progress, schema back to In Progress | API turns Blocked, toast explains | pass |
| Suggest dependencies (no API key) | 12 proposed, 1 shown (*Security review → Production release*), 10 dropped as duplicates, 1 as a loop | pass |
| Accept the suggestion | link saved with source `ai`, precision 100% | pass |
| Graph view | layered DAG, critical chain highlighted, pending suggestions dashed | pass |
| 390 px wide screen | columns scroll horizontally, panels full width | pass |
| Browser console | no errors | pass |

## Known failure cases

These are behaviours we know are wrong or weak, found while testing:

1. **Early finishes are not pulled in.** Scheduling is push-only. If a task
   finishes early, dependents keep their dates until someone edits them.
2. **Calendar days only.** A 3-day task over a weekend still ends 2 days
   later. There is no working-day calendar or holiday list.
3. **Multi-process SQLite can race.** The in-process lock only covers one
   server process. Two processes on the same SQLite file could validate
   against the same old graph. PostgreSQL's advisory lock covers this; use it
   for any multi-instance deployment.
4. **Done dependents after a regression.** If a prerequisite moves back while
   a dependent is already Done, the dependent stays in Done with a Blocked
   badge. That is intentional (no silent moves) but can look contradictory.
5. **Reversed AI suggestions.** The keyword model matches words, not meaning.
   It can propose a link in the wrong direction; gate 5 only drops it if it
   creates a loop (as happens with *Production release → UAT* on the seed
   data). Otherwise a person has to reject it.
6. **Paraphrased LLM evidence is dropped.** If the model paraphrases instead
   of quoting, gate 4 drops a possibly correct suggestion. We prefer losing a
   suggestion to showing an ungrounded one.
7. **Large boards.** Each write returns the full board. Beyond a few thousand
   tasks the payload and client re-render become noticeable.
8. **Rate limit resets on restart** because it is kept in memory.
9. **Dates before a prerequisite are rejected, not corrected.** Some users
   might expect the app to move the date for them.
10. **Seed dates use the server's local date**, so a server in another time
    zone can seed a board that starts a day earlier or later.
