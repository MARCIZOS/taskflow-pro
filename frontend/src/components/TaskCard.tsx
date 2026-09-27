import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { dateRange } from "../lib";
import type { Task } from "../types";

interface Props {
  task: Task;
  blockerNames: string[];
  onOpen: (id: number) => void;
  highlightCritical: boolean;
  overlay?: boolean;
}

export function TaskCard({ task, blockerNames, onOpen, highlightCritical, overlay }: Props) {
  const sortable = useSortable({ id: task.id, disabled: overlay });
  const style = overlay
    ? undefined
    : { transform: CSS.Translate.toString(sortable.transform), transition: sortable.transition };

  const state = task.is_blocked ? "blocked" : "ready";
  const critical = highlightCritical && task.is_critical;

  return (
    <article
      ref={overlay ? undefined : sortable.setNodeRef}
      style={style}
      className={[
        "card",
        `card--${state}`,
        critical ? "card--critical" : "",
        sortable.isDragging ? "card--ghost" : "",
        overlay ? "card--overlay" : "",
      ].join(" ")}
      {...(overlay ? {} : sortable.attributes)}
      {...(overlay ? {} : sortable.listeners)}
      onClick={() => onOpen(task.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onOpen(task.id);
      }}
      aria-label={`${task.title}, ${state}${critical ? ", on the critical path" : ""}`}
    >
      <header className="card__top">
        <span className={`pill pill--${state}`}>{task.is_blocked ? "Blocked" : "Ready"}</span>
        {critical && <span className="pill pill--critical" title="Zero slack: any delay here moves the end date">Critical</span>}
        <span className="card__id">#{task.id}</span>
      </header>
      <h3 className="card__title">{task.title}</h3>
      {task.is_blocked && (
        <p className="card__waiting" title={blockerNames.join(", ")}>
          Waiting on {blockerNames.length === 1 ? blockerNames[0] : `${blockerNames.length} tasks`}
        </p>
      )}
      <footer className="card__meta">
        <span className="mono">{dateRange(task.start_date, task.end_date)}</span>
        <span>{task.duration_days}d</span>
        {task.predecessor_ids.length + task.successor_ids.length > 0 && (
          <span title="prerequisites / dependents">
            ↑{task.predecessor_ids.length} ↓{task.successor_ids.length}
          </span>
        )}
        {!critical && task.slack_days !== null && task.slack_days > 0 && (
          <span className="card__slack" title="Days this task can slip without moving the end date">
            +{task.slack_days}d slack
          </span>
        )}
      </footer>
    </article>
  );
}
