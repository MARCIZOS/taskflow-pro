import { GATED, type Board, type Status, type Task } from "./types";

/** Tasks grouped by column, each column sorted the way the server stores it. */
export function groupByStatus(tasks: Task[]): Record<Status, Task[]> {
  const out: Record<Status, Task[]> = { backlog: [], in_progress: [], review: [], done: [] };
  for (const t of tasks) out[t.status].push(t);
  for (const list of Object.values(out)) list.sort((a, b) => a.position - b.position || a.id - b.id);
  return out;
}

/**
 * Board as it should look right after a card is dropped, before the server
 * answers. Used for optimistic updates; the server's reply replaces it.
 */
export function applyLocalMove(board: Board, taskId: number, status: Status, index: number): Board {
  const columns = groupByStatus(board.tasks);
  const task = board.tasks.find((t) => t.id === taskId);
  if (!task) return board;
  columns[task.status] = columns[task.status].filter((t) => t.id !== taskId);
  const target = columns[status].filter((t) => t.id !== taskId);
  const clamped = Math.max(0, Math.min(index, target.length));
  target.splice(clamped, 0, { ...task, status });
  columns[status] = target;
  const tasks = (Object.keys(columns) as Status[]).flatMap((s) =>
    columns[s].map((t, i) => ({ ...t, position: (i + 1) * 1024 })),
  );
  return { ...board, tasks };
}

/** Titles of unfinished prerequisites that stop a task from entering `status`. */
export function blockersFor(board: Board, task: Task, status: Status): string[] {
  if (!GATED.has(status)) return [];
  const byId = new Map(board.tasks.map((t) => [t.id, t]));
  return task.blocked_by.map((id) => byId.get(id)?.title ?? `#${id}`);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-04" -> "Oct 4". Parsed by hand so time zones can't shift the day. */
export function shortDate(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

export function dateRange(start: string, end: string): string {
  return start === end ? shortDate(start) : `${shortDate(start)} – ${shortDate(end)}`;
}

export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

export function todayIso(): string {
  const now = new Date();
  return new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())).toISOString().slice(0, 10);
}

/**
 * Layer tasks for the graph view: each task sits one column to the right of
 * its deepest prerequisite (longest-path layering), rows keep a stable order.
 */
export function layerTasks(tasks: Task[]): Map<number, { layer: number; row: number }> {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const depth = new Map<number, number>();
  const visiting = new Set<number>();
  const visit = (id: number): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // defensive; the server guarantees a DAG
    visiting.add(id);
    const preds = byId.get(id)?.predecessor_ids.filter((p) => byId.has(p)) ?? [];
    const d = preds.length ? Math.max(...preds.map(visit)) + 1 : 0;
    visiting.delete(id);
    depth.set(id, d);
    return d;
  };
  tasks.forEach((t) => visit(t.id));
  const rows = new Map<number, number>();
  const out = new Map<number, { layer: number; row: number }>();
  [...tasks]
    .sort((a, b) => a.start_date.localeCompare(b.start_date) || a.id - b.id)
    .forEach((t) => {
      const layer = depth.get(t.id) ?? 0;
      const row = rows.get(layer) ?? 0;
      rows.set(layer, row + 1);
      out.set(t.id, { layer, row });
    });
  return out;
}
