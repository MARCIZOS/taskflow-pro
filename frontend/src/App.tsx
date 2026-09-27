import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "./api";
import { BoardView } from "./components/BoardView";
import { GraphView } from "./components/GraphView";
import { NewTaskDialog } from "./components/NewTaskDialog";
import { SuggestionsPanel } from "./components/SuggestionsPanel";
import { TaskDrawer } from "./components/TaskDrawer";
import { SUGGESTIONS_KEY, useBoard } from "./hooks";
import { dateRange } from "./lib";

type View = "board" | "graph";

export default function App() {
  const board = useBoard();
  const pending = useQuery({ queryKey: SUGGESTIONS_KEY, queryFn: api.pendingSuggestions });
  const [view, setView] = useState<View>("board");
  const [openId, setOpenId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [showCritical, setShowCritical] = useState(true);

  const data = board.data;
  const openTask = data?.tasks.find((t) => t.id === openId);
  const blocked = data?.tasks.filter((t) => t.is_blocked && t.status !== "done").length ?? 0;
  const pendingCount = pending.data?.length ?? 0;

  return (
    <div className={`app ${aiOpen ? "app--with-panel" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark" aria-hidden>
            <svg viewBox="0 0 32 32" width="26" height="26">
              <rect width="32" height="32" rx="7" fill="currentColor" />
              <path d="M8 10h7v5H8zM17 17h7v5h-7z" fill="var(--paper)" />
              <path d="M15 12.5h2.5v7H17" stroke="var(--paper)" strokeWidth="1.6" fill="none" />
            </svg>
          </span>
          <div>
            <h1>TaskFlow Pro</h1>
            {data?.project_start && data.project_end && (
              <p className="muted">
                Plan: <span className="mono">{dateRange(data.project_start, data.project_end)}</span>
                {" · "}
                {data.tasks.length} tasks · {blocked} blocked
              </p>
            )}
          </div>
        </div>

        <nav className="topbar__actions">
          <div className="segmented" role="tablist" aria-label="View">
            <button role="tab" aria-selected={view === "board"} className={view === "board" ? "on" : ""} onClick={() => setView("board")}>
              Board
            </button>
            <button role="tab" aria-selected={view === "graph"} className={view === "graph" ? "on" : ""} onClick={() => setView("graph")}>
              Dependency graph
            </button>
          </div>
          <label className="toggle">
            <input type="checkbox" checked={showCritical} onChange={(e) => setShowCritical(e.target.checked)} />
            Critical path
          </label>
          <button className={`btn ${aiOpen ? "btn--active" : ""}`} onClick={() => setAiOpen((v) => !v)}>
            AI suggestions{pendingCount > 0 && <span className="badge">{pendingCount}</span>}
          </button>
          <button className="btn btn--primary" onClick={() => setCreating(true)}>
            + New task
          </button>
        </nav>
      </header>

      <main className="main">
        {board.isLoading && <div className="state">Loading the board…</div>}
        {board.isError && (
          <div className="state state--error">
            <p>Couldn't load the board. Is the API running?</p>
            <button className="btn" onClick={() => board.refetch()}>
              Try again
            </button>
          </div>
        )}
        {data && data.tasks.length === 0 && (
          <div className="state">
            <p>No tasks yet.</p>
            <button className="btn btn--primary" onClick={() => setCreating(true)}>
              Create the first task
            </button>
          </div>
        )}
        {data && data.tasks.length > 0 &&
          (view === "board" ? (
            <BoardView board={data} onOpen={setOpenId} showCritical={showCritical} />
          ) : (
            <GraphView board={data} onOpen={setOpenId} showCritical={showCritical} />
          ))}
      </main>

      {aiOpen && <SuggestionsPanel onClose={() => setAiOpen(false)} />}
      {data && openTask && <TaskDrawer board={data} task={openTask} onClose={() => setOpenId(null)} onOpen={setOpenId} />}
      {creating && <NewTaskDialog onClose={() => setCreating(false)} onCreated={setOpenId} />}
    </div>
  );
}
