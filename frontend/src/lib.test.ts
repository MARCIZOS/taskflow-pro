import { describe, expect, it } from "vitest";
import { addDays, applyLocalMove, blockersFor, dateRange, groupByStatus, layerTasks, shortDate } from "./lib";
import type { Board, Task } from "./types";

function task(id: number, over: Partial<Task> = {}): Task {
  return {
    id,
    title: `T${id}`,
    description: "",
    status: "backlog",
    position: id * 1024,
    start_date: "2026-10-01",
    end_date: "2026-10-02",
    duration_days: 2,
    version: 1,
    predecessor_ids: [],
    successor_ids: [],
    blocked_by: [],
    is_blocked: false,
    slack_days: 0,
    is_critical: false,
    ...over,
  };
}

const board = (tasks: Task[]): Board => ({
  tasks,
  dependencies: [],
  critical_chain: [],
  project_start: null,
  project_end: null,
});

describe("groupByStatus", () => {
  it("sorts each column by position", () => {
    const cols = groupByStatus([task(1, { position: 3 }), task(2, { position: 1 }), task(3, { status: "done" })]);
    expect(cols.backlog.map((t) => t.id)).toEqual([2, 1]);
    expect(cols.done.map((t) => t.id)).toEqual([3]);
  });
});

describe("applyLocalMove", () => {
  it("moves a card to another column at the given index", () => {
    const b = board([task(1), task(2), task(3, { status: "review" })]);
    const next = applyLocalMove(b, 1, "review", 0);
    const cols = groupByStatus(next.tasks);
    expect(cols.review.map((t) => t.id)).toEqual([1, 3]);
    expect(cols.backlog.map((t) => t.id)).toEqual([2]);
  });

  it("reorders inside the same column", () => {
    const b = board([task(1), task(2), task(3)]);
    const cols = groupByStatus(applyLocalMove(b, 3, "backlog", 0).tasks);
    expect(cols.backlog.map((t) => t.id)).toEqual([3, 1, 2]);
  });
});

describe("blockersFor", () => {
  it("only gates In Progress, Review and Done", () => {
    const a = task(1);
    const b = task(2, { blocked_by: [1], is_blocked: true });
    const bd = board([a, b]);
    expect(blockersFor(bd, b, "in_progress")).toEqual(["T1"]);
    expect(blockersFor(bd, b, "backlog")).toEqual([]);
  });
});

describe("dates", () => {
  it("formats without time-zone drift", () => {
    expect(shortDate("2026-10-04")).toBe("Oct 4");
    expect(dateRange("2026-10-04", "2026-10-04")).toBe("Oct 4");
    expect(addDays("2026-10-30", 3)).toBe("2026-11-02");
  });
});

describe("layerTasks", () => {
  it("places each task after its deepest prerequisite", () => {
    const tasks = [
      task(1),
      task(2, { predecessor_ids: [1] }),
      task(3, { predecessor_ids: [1] }),
      task(4, { predecessor_ids: [2, 3] }),
    ];
    const layers = layerTasks(tasks);
    expect([1, 2, 3, 4].map((id) => layers.get(id)!.layer)).toEqual([0, 1, 1, 2]);
    expect(layers.get(2)!.row).not.toBe(layers.get(3)!.row);
  });
});
