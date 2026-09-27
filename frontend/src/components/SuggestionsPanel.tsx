import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { METRICS_KEY, reportError, SUGGESTIONS_KEY, useRunMutation } from "../hooks";
import type { SuggestRun } from "../types";
import { useToast } from "./Toasts";

const GATE_LABELS: Record<string, string> = {
  bad_format: "wrong format",
  unknown_task: "unknown task id",
  duplicate_or_self: "already linked / self link",
  evidence_not_found: "evidence not in task text",
  would_create_cycle: "would create a loop",
};

export function SuggestionsPanel({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const run = useRunMutation();
  const [lastRun, setLastRun] = useState<SuggestRun | null>(null);
  const [running, setRunning] = useState(false);

  const pending = useQuery({ queryKey: SUGGESTIONS_KEY, queryFn: api.pendingSuggestions });
  const metrics = useQuery({ queryKey: METRICS_KEY, queryFn: api.aiMetrics });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: SUGGESTIONS_KEY });
    qc.invalidateQueries({ queryKey: METRICS_KEY });
  };

  const ask = async () => {
    setRunning(true);
    try {
      const result = await api.suggest();
      setLastRun(result);
      qc.setQueryData(SUGGESTIONS_KEY, result.suggestions);
      qc.invalidateQueries({ queryKey: METRICS_KEY });
    } catch (e) {
      reportError(toast, e);
    } finally {
      setRunning(false);
    }
  };

  const accept = async (id: number) => {
    await run(() => api.acceptSuggestion(id), "Dependency added");
    refresh();
  };

  const reject = async (id: number) => {
    try {
      await api.rejectSuggestion(id);
    } catch (e) {
      reportError(toast, e);
    }
    refresh();
  };

  const items = pending.data ?? [];
  const dropped = lastRun ? Object.entries(lastRun.dropped).filter(([, n]) => n > 0) : [];

  return (
    <aside className="ai-panel" aria-label="AI dependency suggestions">
      <header className="ai-panel__head">
        <div>
          <p className="eyebrow">Assistant</p>
          <h2>Suggested dependencies</h2>
        </div>
        <button className="icon-btn" aria-label="Close suggestions" onClick={onClose}>
          ×
        </button>
      </header>

      <p className="hint">
        Reads task titles and descriptions and proposes links. Nothing is added until you accept it, and the engine
        re-checks every accepted link for loops.
      </p>
      <button className="btn btn--primary btn--block" onClick={ask} disabled={running}>
        {running ? "Reading tasks…" : "Suggest dependencies"}
      </button>

      {lastRun && (
        <div className="ai-run">
          <p>
            <strong>{lastRun.proposed}</strong> proposed · <strong>{lastRun.shown}</strong> passed the checks
            <span className="muted"> · {lastRun.provider}</span>
          </p>
          {lastRun.used_fallback && (
            <p className="muted">Using the offline keyword model ({lastRun.fallback_reason}).</p>
          )}
          {dropped.length > 0 && (
            <ul className="ai-run__drops">
              {dropped.map(([gate, n]) => (
                <li key={gate}>
                  {n} dropped: {GATE_LABELS[gate] ?? gate}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="ai-list">
        {items.length === 0 && !running && <p className="muted">No suggestions waiting for review.</p>}
        {items.map((s) => (
          <article key={s.id} className="suggestion">
            <p className="suggestion__link">
              <span>{s.predecessor_title}</span>
              <span className="arrow" aria-hidden>
                →
              </span>
              <span>{s.successor_title}</span>
            </p>
            <p className="suggestion__why">{s.rationale}</p>
            <blockquote>“{s.evidence}”</blockquote>
            <div className="confidence" title={`Confidence ${Math.round(s.confidence * 100)}%`}>
              <span style={{ width: `${Math.round(s.confidence * 100)}%` }} />
            </div>
            <div className="suggestion__actions">
              <button className="btn btn--primary btn--small" onClick={() => accept(s.id)}>
                Accept
              </button>
              <button className="btn btn--small" onClick={() => reject(s.id)}>
                Reject
              </button>
            </div>
          </article>
        ))}
      </div>

      {metrics.data && metrics.data.accepted + metrics.data.rejected > 0 && (
        <footer className="ai-panel__foot muted">
          Precision so far: {Math.round((metrics.data.precision ?? 0) * 100)}% ({metrics.data.accepted} accepted,{" "}
          {metrics.data.rejected} rejected)
        </footer>
      )}
    </aside>
  );
}
