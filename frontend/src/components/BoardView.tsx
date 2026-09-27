import {
  closestCorners,
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { BOARD_KEY, useRunMutation } from "../hooks";
import { applyLocalMove, blockersFor, groupByStatus } from "../lib";
import { COLUMNS, type Board, type Status } from "../types";
import { TaskCard } from "./TaskCard";
import { useToast } from "./Toasts";

type Layout = Record<Status, number[]>;

const colId = (s: Status) => `col:${s}`;
const isColId = (id: unknown): id is string => typeof id === "string" && id.startsWith("col:");

function layoutOf(board: Board): Layout {
  const cols = groupByStatus(board.tasks);
  return {
    backlog: cols.backlog.map((t) => t.id),
    in_progress: cols.in_progress.map((t) => t.id),
    review: cols.review.map((t) => t.id),
    done: cols.done.map((t) => t.id),
  };
}

function findColumn(layout: Layout, id: unknown): Status | null {
  if (isColId(id)) return id.slice(4) as Status;
  return (Object.keys(layout) as Status[]).find((s) => layout[s].includes(id as number)) ?? null;
}

interface Props {
  board: Board;
  onOpen: (id: number) => void;
  showCritical: boolean;
}

export function BoardView({ board, onOpen, showCritical }: Props) {
  const qc = useQueryClient();
  const toast = useToast();
  const run = useRunMutation();
  const [layout, setLayout] = useState<Layout>(() => layoutOf(board));
  const [activeId, setActiveId] = useState<number | null>(null);

  // Follow the server's board whenever we are not in the middle of a drag.
  useEffect(() => {
    if (activeId === null) setLayout(layoutOf(board));
  }, [board, activeId]);

  const byId = useMemo(() => new Map(board.tasks.map((t) => [t.id, t])), [board]);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragStart = (e: DragStartEvent) => setActiveId(e.active.id as number);

  // Move the card between columns while it is being dragged, so the target
  // column opens up a gap under the pointer.
  const onDragOver = ({ active, over }: DragOverEvent) => {
    if (!over) return;
    const from = findColumn(layout, active.id);
    const to = findColumn(layout, over.id);
    if (!from || !to || from === to) return;
    setLayout((prev) => {
      const target = prev[to].filter((id) => id !== active.id);
      const overIndex = isColId(over.id) ? target.length : target.indexOf(over.id as number);
      target.splice(overIndex < 0 ? target.length : overIndex, 0, active.id as number);
      return { ...prev, [from]: prev[from].filter((id) => id !== active.id), [to]: target };
    });
  };

  const onDragEnd = async ({ active, over }: DragEndEvent) => {
    const taskId = active.id as number;
    setActiveId(null);
    const task = byId.get(taskId);
    const to = over ? findColumn(layout, over.id) : null;
    if (!task || !to) {
      setLayout(layoutOf(board));
      return;
    }
    // After onDragOver the card already sits in the target column; a final
    // reorder within that column gives its drop index.
    let finalIds = layout[to].includes(taskId) ? layout[to] : [...layout[to], taskId];
    if (!isColId(over!.id)) {
      const from = finalIds.indexOf(taskId);
      const target = finalIds.indexOf(over!.id as number);
      if (target !== -1 && from !== target) finalIds = arrayMove(finalIds, from, target);
    }
    const index = finalIds.indexOf(taskId);

    const original = layoutOf(board);
    if (to === task.status && original[to].indexOf(taskId) === index) {
      setLayout(original);
      return;
    }

    // Fast feedback for the common case; the server enforces the same rule.
    const blockers = blockersFor(board, task, to);
    if (blockers.length) {
      setLayout(original);
      toast.show("error", "Task is blocked", [`Finish ${blockers.join(", ")} before moving '${task.title}'.`]);
      return;
    }

    // Optimistic update, rolled back by the refetch in useRunMutation on error.
    qc.setQueryData<Board>(BOARD_KEY, (b) => (b ? applyLocalMove(b, taskId, to, index) : b));
    await run(() => api.moveTask(taskId, task.version, to, index));
  };

  const active = activeId !== null ? byId.get(activeId) : undefined;

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragEnd={onDragEnd}
      onDragCancel={() => {
        setActiveId(null);
        setLayout(layoutOf(board));
      }}
    >
      <div className="board">
        {COLUMNS.map((col) => (
          <Column key={col.status} status={col.status} label={col.label} hint={col.hint} count={layout[col.status].length}>
            <SortableContext items={layout[col.status]} strategy={verticalListSortingStrategy}>
              {layout[col.status].map((id) => {
                const t = byId.get(id);
                if (!t) return null;
                return (
                  <TaskCard
                    key={id}
                    task={t}
                    blockerNames={blockersFor(board, t, "in_progress")}
                    onOpen={onOpen}
                    highlightCritical={showCritical}
                  />
                );
              })}
            </SortableContext>
          </Column>
        ))}
      </div>
      <DragOverlay dropAnimation={null}>
        {active ? (
          <TaskCard task={active} blockerNames={[]} onOpen={() => {}} highlightCritical={showCritical} overlay />
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}

function Column(props: { status: Status; label: string; hint: string; count: number; children: React.ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: colId(props.status) });
  return (
    <section className={`column column--${props.status} ${isOver ? "column--over" : ""}`} aria-label={props.label}>
      <header className="column__head">
        <h2>{props.label}</h2>
        <span className="column__count">{props.count}</span>
        <p>{props.hint}</p>
      </header>
      <div ref={setNodeRef} className="column__body">
        {props.children}
        {props.count === 0 && <div className="column__empty">Drop a task here</div>}
      </div>
    </section>
  );
}
