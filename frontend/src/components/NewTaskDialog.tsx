import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useRunMutation } from "../hooks";
import { addDays, todayIso } from "../lib";

export function NewTaskDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const run = useRunMutation();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [start, setStart] = useState(todayIso());
  const [end, setEnd] = useState(addDays(todayIso(), 2));
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => first.current?.focus(), []);

  const invalid = !title.trim() || !start || !end || end < start;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (invalid) return;
    setBusy(true);
    const result = await run(
      () => api.createTask({ title: title.trim(), description: description.trim(), start_date: start, end_date: end }),
    );
    setBusy(false);
    if (result) {
      const created = [...result.board.tasks].sort((a, b) => b.id - a.id)[0];
      onClose();
      if (created) onCreated(created.id);
    }
  };

  return (
    <div className="drawer-backdrop drawer-backdrop--center" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="new-task-title">
        <h2 id="new-task-title">New task</h2>
        <label>
          Title
          <input ref={first} value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Load test the API" />
        </label>
        <label>
          Description
          <textarea
            rows={3}
            value={description}
            maxLength={4000}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Mention what it waits for (e.g. 'after the backend API is built') and AI suggestions can pick it up."
          />
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
        {end < start && <p className="field-error">End date must be on or after the start date.</p>}
        <div className="form__actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn--primary" disabled={invalid || busy}>
            {busy ? "Creating…" : "Create task"}
          </button>
        </div>
      </form>
    </div>
  );
}
