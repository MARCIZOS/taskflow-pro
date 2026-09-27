import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useRunMutation } from "../hooks";
import { dateRange } from "../lib";
import { COLUMNS, type Board, type Task, type ValidationResult } from "../types";

interface Props {
  board: Board;
  task: Task;
  onClose: () => void;
  onOpen: (id: number) => void;
}

export function TaskDrawer({ board, task, onClose, onOpen }: Props) {
  const run = useRunMutation();
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [start, setStart] = useState(task.start_date);
  const [end, setEnd] = useState(task.end_date);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Reload the form when another task is opened or the server moved this one.
  useEffect(() => {
    setTitle(task.title);
    setDescription(task.description);
    setStart(task.start_date);
    setEnd(task.end_date);
    setConfirmDelete(false);
  }, [task.id, task.version]); // only when the task itself changes, not on every render

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const byId = useMemo(() => new Map(board.tasks.map((t) => [t.id, t])), [board]);
  const depFor = (pred: number, succ: number) =>
    board.dependencies.find((d) => d.predecessor_id === pred && d.successor_id === succ);

  const dirty =
    title.trim() !== task.title || description !== task.description || start !== task.start_date || end !== task.end_date;
  const datesInvalid = !start || !end || end < start;

  const save = async () => {
    setSaving(true);
    const patch: Parameters<typeof api.updateTask>[1] = { version: task.version };
    if (title.trim() !== task.title) patch.title = title.trim();
    if (description !== task.description) patch.description = description;
    if (start !== task.start_date) patch.start_date = start;
    if (end !== task.end_date) patch.end_date = end;
    await run(() => api.updateTask(task.id, patch));
    setSaving(false);
  };

  const remove = async () => {
    const ok = await run(() => api.deleteTask(task.id));
    if (ok) onClose();
  };

  const column = COLUMNS.find((c) => c.status === task.status)?.label;

  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header className="drawer__head">
          <div>
            <p className="eyebrow">
              #{task.id} · {column} · {task.is_blocked ? "Blocked" : "Ready"}
              {task.is_critical ? " · Critical path" : ""}
            </p>
            <h2 id="drawer-title">{task.title}</h2>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="drawer__scroll">
          <section className="form">
            <label>
              Title
              <input value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} />
            </label>
            <label>
              Description
              <textarea rows={3} value={description} maxLength={4000} onChange={(e) => setDescription(e.target.value)} />
            </label>
            <div className="form__row">
              <label>
                Start
                <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
              </label>
              <label>
                End
                <input type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
              </label>
            </div>
            {datesInvalid && <p className="field-error">End date must be on or after the start date.</p>}
            <p className="hint">
              Changing dates moves every later task that depends on this one, just enough to keep the plan valid.
              {task.slack_days !== null && task.slack_days > 0 && ` This task has ${task.slack_days} day(s) of slack.`}
            </p>
            <div className="form__actions">
              <button className="btn btn--primary" disabled={!dirty || datesInvalid || !title.trim() || saving} onClick={save}>
                {saving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </section>

          <section>
            <h3 className="section-title">Prerequisites</h3>
            {task.predecessor_ids.length === 0 && <p className="muted">None. This task can start any time.</p>}
            <ul className="dep-list">
              {task.predecessor_ids.map((pid) => {
                const p = byId.get(pid);
                const dep = depFor(pid, task.id);
                if (!p) return null;
                return (
                  <li key={pid}>
                    <button className="link" onClick={() => onOpen(pid)}>
                      {p.title}
                    </button>
                    <span className={`pill pill--small ${p.status === "done" ? "pill--ready" : "pill--blocked"}`}>
                      {p.status === "done" ? "Done" : "Not done"}
                    </span>
                    <span className="muted mono">ends {dateRange(p.end_date, p.end_date)}</span>
                    {dep?.source === "ai" && <span className="pill pill--small pill--ai">AI</span>}
                    {dep && (
                      <button
                        className="icon-btn icon-btn--small"
                        aria-label={`Remove prerequisite ${p.title}`}
                        onClick={() => run(() => api.removeDependency(dep.id))}
                      >
                        ×
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
            <AddPrerequisite board={board} task={task} />
          </section>

          <section>
            <h3 className="section-title">Tasks waiting on this one</h3>
            {task.successor_ids.length === 0 ? (
              <p className="muted">Nothing depends on this task yet.</p>
            ) : (
              <ul className="dep-list">
                {task.successor_ids.map((sid) => {
                  const s = byId.get(sid);
                  return s ? (
                    <li key={sid}>
                      <button className="link" onClick={() => onOpen(sid)}>
                        {s.title}
                      </button>
                      <span className="muted mono">starts {dateRange(s.start_date, s.start_date)}</span>
                    </li>
                  ) : null;
                })}
              </ul>
            )}
          </section>

          <section className="danger-zone">
            {confirmDelete ? (
              <>
                <p>
                  Delete <strong>{task.title}</strong>? Its links are removed too
                  {task.successor_ids.length ? `, and ${task.successor_ids.length} task(s) lose a prerequisite` : ""}.
                </p>
                <button className="btn btn--danger" onClick={remove}>
                  Yes, delete it
                </button>
                <button className="btn" onClick={() => setConfirmDelete(false)}>
                  Keep it
                </button>
              </>
            ) : (
              <button className="btn btn--ghost-danger" onClick={() => setConfirmDelete(true)}>
                Delete task
              </button>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}

function AddPrerequisite({ board, task }: { board: Board; task: Task }) {
  const run = useRunMutation();
  const [pick, setPick] = useState<number | "">("");
  const [check, setCheck] = useState<ValidationResult | null>(null);
  const [busy, setBusy] = useState(false);

  const options = board.tasks
    .filter((t) => t.id !== task.id && !task.predecessor_ids.includes(t.id))
    .sort((a, b) => a.title.localeCompare(b.title));

  // Dry-run the link as soon as it is picked, so a loop is explained before
  // anyone presses Add.
  useEffect(() => {
    setCheck(null);
    if (pick === "") return;
    let live = true;
    api
      .validateDependency(pick, task.id)
      .then((r) => live && setCheck(r))
      .catch((e) => live && setCheck({ valid: false, code: "error", message: e instanceof ApiError ? e.message : String(e), cycle: null }));
    return () => {
      live = false;
    };
  }, [pick, task.id]);

  const add = async () => {
    if (pick === "") return;
    setBusy(true);
    const ok = await run(() => api.addDependency(pick, task.id));
    setBusy(false);
    if (ok) setPick("");
  };

  return (
    <div className="add-dep">
      <select value={pick} onChange={(e) => setPick(e.target.value ? Number(e.target.value) : "")} aria-label="Add a prerequisite">
        <option value="">Add a prerequisite…</option>
        {options.map((t) => (
          <option key={t.id} value={t.id}>
            {t.title}
          </option>
        ))}
      </select>
      <button className="btn" disabled={!check?.valid || busy} onClick={add}>
        Add
      </button>
      {check && !check.valid && <p className="field-error">{check.message}</p>}
      {check?.valid && <p className="field-ok">OK, no loop. Dates will adjust if needed.</p>}
    </div>
  );
}
