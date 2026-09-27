# TaskFlow Pro: design document

Architecture, data model and known limitations. This covers the "combined
design document" item of the submission checklist.

## 1. Architecture

```
 Browser (React + TypeScript)
 ┌──────────────────────────────────────────────────────────────┐
 │ BoardView (dnd-kit)   TaskDrawer   SuggestionsPanel  GraphView│
 │        │ optimistic update + rollback     (React Flow)        │
 │        ▼                                                      │
 │ TanStack Query cache  ◄── every write returns the new board   │
 └──────────────┬───────────────────────────────────────────────┘
                │ JSON over HTTP (/api/...)
 ┌──────────────▼───────────────────────────────────────────────┐
 │ FastAPI  (app/api.py, app/main.py)                            │
 │   thin routes, Pydantic validation, uniform error format      │
 ├───────────────────────────────────────────────────────────────┤
 │ Services                                                      │
 │   board.py  load snapshot → ask engine → save in 1 transaction│
 │   ai.py     provider → 5 validation gates → pending → accept  │
 ├───────────────────────────────────────────────────────────────┤
 │ Engine (app/engine/graph.py)  pure functions, no I/O          │
 │   validate_new_edge · readiness · propagate_schedule ·        │
 │   critical_path · topological_order                           │
 ├───────────────────────────────────────────────────────────────┤
 │ SQLAlchemy → SQLite (dev) / PostgreSQL (prod)                 │
 └───────────────────────────────────────────────────────────────┘
```

The central decision is that **all graph rules live in one module with no
database or web code**. It takes plain ids, dates and edges and returns
answers. That makes each rule easy to test in isolation (including with
property-based tests), and it means the UI and the AI can never bypass it: both
go through the same service functions, which always consult the engine before
writing.

### Request flow for a write

1. The route validates the body with Pydantic.
2. The service takes the graph write lock (an in-process lock, plus a
   PostgreSQL transaction-scoped advisory lock) and loads a snapshot of tasks
   and dependencies.
3. It checks the optimistic-lock `version` the client sent.
4. The engine validates the change and computes consequences (blockers,
   downstream date moves, newly blocked tasks).
5. If the engine refuses, a `ServiceError` becomes a 4xx response with a
   human-readable message and structured detail (for example the loop's task
   ids). Nothing has been written.
6. Otherwise the changes and a change-log entry are committed in one
   transaction and the new board is returned with a list of schedule changes.

## 2. Data model

| Table | Columns | Notes |
|---|---|---|
| `tasks` | id, title, description, status, position, start_date, end_date, version, created_at, updated_at | `status ∈ {backlog, in_progress, review, done}` (check constraint), `end_date ≥ start_date` (check), `version` is SQLAlchemy's optimistic-lock column, index on (status, position) |
| `dependencies` | id, predecessor_id → tasks, successor_id → tasks, source, created_at | unique (predecessor, successor), check predecessor ≠ successor, cascade on task delete, `source ∈ {seed, manual, ai}` |
| `ai_suggestions` | id, predecessor_id, successor_id, confidence, rationale, evidence, model, status, created_at, decided_at | `status ∈ {pending, accepted, rejected, stale}`; kept after decisions to measure precision |
| `change_log` | id, task_id, kind, detail (JSON), created_at | append-only audit trail (status changes, date moves, links added/removed) |

What is **not** stored: Blocked/Ready, slack and the critical path. They are
derived from the graph on every read, so a change anywhere upstream can never
leave a stale flag behind.

Card order uses a fractional `position` per column: moving a card writes only
that card (midpoint between its new neighbours). If two neighbours get too
close, that column is renumbered.

## 3. Algorithms

Let *V* be tasks and *E* links.

**Cycle check** (`validate_new_edge`). Adding A → B closes a loop exactly when
A is already reachable from B. A breadth-first search from B answers that in
O(V + E) and also yields the *shortest* loop, which is returned to the UI, for
example `Production release → Gather requirements → … → Production release`.
The graph is never modified, so the same function serves as the dry-run
endpoint. Self links and duplicates are rejected before the search.

**Blocked / Ready** (`readiness`). A task is Ready when every prerequisite is
Done. O(V + E) for the whole board.

**Status gate** (`check_status_change`). Moving to Backlog is always allowed;
moving to In Progress, Review or Done requires no blockers.

**Rollback on regression** (`newly_blocked_by_regression`). Readiness is
computed before and after a status change; tasks that went from Ready to
Blocked are reported so the UI can say so.

**Schedule propagation without compounding** (`propagate_schedule`).

1. Collect the descendants of the changed task (DFS).
2. Order changed task + descendants topologically (Kahn's algorithm).
3. For each descendant in that order:
   `earliest = max(end of each prerequisite) + 1 day`. If the task starts
   earlier than that, shift it (start and end together, keeping its duration).

Because each task is visited once, after all of its prerequisites are final,
and its start is a *maximum* over prerequisites rather than a sum of deltas
along paths, converging paths cannot double count. In the problem's diamond
(A → B → D, A → C → D, A +3 days) D moves 3 days. If B had spare time and C
did not, D moves only by the real impact. Cost O(V + E) over the affected
subgraph only.

Adding a link can also make the successor start too early; the service shifts
the successor to the day after the predecessor ends and then propagates from
it with the same function.

**Critical path** (`critical_path`). A backward pass over the topological order
computes each task's latest start given the project end
(`latest finish = min(latest start of successors) − 1`). Slack is latest start
minus scheduled start; zero-slack tasks are critical, and a chain of them is
walked from the earliest one. O(V + E).

## 4. AI dependency suggestions

```
tasks + existing links ──► provider ──► raw JSON ──► 5 gates ──► pending ──► person ──► accept ──► engine
                           (OpenAI /                                                     (same path as
                            Anthropic /                                                   a manual link)
                            keyword)
```

* **Prompt**: system prompt defines finish-to-start, forbids invented ids and
  links without a concrete textual reason, and requires an `evidence` phrase
  copied word for word; one short worked example; temperature 0; JSON output.
* **Gates**: (1) schema check with Pydantic, (2) both ids exist, (3) not a
  self link, duplicate, existing or already-pending link, (4) the evidence
  phrase really occurs in the two tasks' text (normalised), (5) engine dry run,
  also against the other suggestions accepted in the same batch, so a batch
  cannot form a loop on its own.
* **Human in the loop**: survivors are stored as `pending` and shown as dashed
  links with the reason, the quoted evidence and a confidence bar. Accepting
  calls `board.add_dependency`, so the engine re-validates against the graph as
  it is *now*; if it changed meanwhile, the suggestion becomes `stale`.
* **Resilience**: provider errors, timeouts or a missing key fall back to the
  keyword model, and the response says so. Calls are rate limited.
* **Measurement**: `/api/ai/metrics` reports precision = accepted / (accepted +
  rejected).

## 5. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/board` | tasks with derived status, links, critical chain |
| POST | `/api/tasks` | create |
| PATCH | `/api/tasks/{id}` | edit title/description/dates (needs `version`) |
| POST | `/api/tasks/{id}/move` | change column and/or order (needs `version`) |
| DELETE | `/api/tasks/{id}` | delete task and its links |
| POST | `/api/dependencies` | add link (409 with `cycle` on loops) |
| POST | `/api/dependencies/validate` | dry run, nothing saved |
| DELETE | `/api/dependencies/{id}` | remove link |
| POST | `/api/ai/suggestions` | run suggestion model |
| GET | `/api/ai/suggestions?status=` | list suggestions |
| POST | `/api/ai/suggestions/{id}/accept` · `/reject` | human decision |
| GET | `/api/ai/metrics` | precision |
| GET | `/api/history` | recent change log |
| POST | `/api/demo/reset` | restore demo data (only if `ALLOW_DEMO_RESET`) |

Errors always look like `{"error": {"code": "...", "message": "...", ...}}`.

## 6. Security and production readiness

* No secrets in the repository; LLM keys come from environment variables and
  stay on the server. `.env` is git-ignored; `.env.example` documents names only.
* All input goes through Pydantic models with `extra="forbid"` and length
  limits; SQL only via the ORM; React escapes task text on render.
* CORS limited to configured origins; methods limited to those used.
* Unhandled errors are logged server-side and returned as a generic 500.
* Database constraints back up the engine (no self links, no duplicates, valid
  dates and statuses).
* Optimistic locking (`version`) stops lost updates from two open tabs; the
  graph lock stops two requests from racing to create a loop.

## 7. Scaling beyond the prototype

* The engine only touches the affected subgraph for propagation. The benchmark
  (`python -m scripts.benchmark`) propagates a delay through 1,000 tasks /
  3,000 links in under 1 ms and computes the critical path in about 3 ms.
* The backend is stateless apart from the database, so it can run several
  instances behind a load balancer with PostgreSQL.
* Next steps would be: per-project graphs (the lock and snapshot become
  per-project), returning only changed tasks instead of the full board,
  websockets for live updates, working-day calendars and lag on links.

## 8. Known limitations

See "Key assumptions and limitations" in the README and "Known failure cases"
in [TESTING.md](TESTING.md).
