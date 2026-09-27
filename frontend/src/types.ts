export type Status = "backlog" | "in_progress" | "review" | "done";

export const COLUMNS: { status: Status; label: string; hint: string }[] = [
  { status: "backlog", label: "Backlog", hint: "Planned, not started" },
  { status: "in_progress", label: "In Progress", hint: "Being worked on" },
  { status: "review", label: "Review", hint: "Waiting for sign-off" },
  { status: "done", label: "Done", hint: "Finished" },
];

// Columns a task can only enter once all its prerequisites are Done.
export const GATED: ReadonlySet<Status> = new Set<Status>(["in_progress", "review", "done"]);

export interface Task {
  id: number;
  title: string;
  description: string;
  status: Status;
  position: number;
  start_date: string; // ISO yyyy-mm-dd
  end_date: string;
  duration_days: number;
  version: number;
  predecessor_ids: number[];
  successor_ids: number[];
  blocked_by: number[];
  is_blocked: boolean;
  slack_days: number | null;
  is_critical: boolean;
}

export interface Dependency {
  id: number;
  predecessor_id: number;
  successor_id: number;
  source: "manual" | "ai" | "seed" | string;
}

export interface Board {
  tasks: Task[];
  dependencies: Dependency[];
  critical_chain: number[];
  project_start: string | null;
  project_end: string | null;
}

export interface ScheduleChange {
  task_id: number;
  title: string;
  old_start: string;
  old_end: string;
  new_start: string;
  new_end: string;
  shift_days: number;
}

export interface MutationResult {
  board: Board;
  schedule_changes: ScheduleChange[];
  newly_blocked: number[];
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  code: string | null;
  message: string | null;
  cycle: number[] | null;
}

export interface Suggestion {
  id: number;
  predecessor_id: number;
  successor_id: number;
  predecessor_title: string;
  successor_title: string;
  confidence: number;
  rationale: string;
  evidence: string;
  model: string;
  status: string;
}

export interface SuggestRun {
  provider: string;
  model: string;
  used_fallback: boolean;
  fallback_reason: string | null;
  proposed: number;
  shown: number;
  dropped: Record<string, number>;
  suggestions: Suggestion[];
}

export interface AIMetrics {
  shown: number;
  accepted: number;
  rejected: number;
  pending: number;
  precision: number | null;
}
