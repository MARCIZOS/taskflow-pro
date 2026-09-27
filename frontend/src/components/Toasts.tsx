import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

type Tone = "info" | "success" | "error";

interface Toast {
  id: number;
  tone: Tone;
  title: string;
  lines?: string[];
}

interface ToastApi {
  show: (tone: Tone, title: string, lines?: string[]) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);

  const show = useCallback(
    (tone: Tone, title: string, lines?: string[]) => {
      const id = nextId.current++;
      setToasts((all) => [...all.slice(-3), { id, tone, title, lines }]);
      // Errors stay a little longer so there is time to read the reason.
      window.setTimeout(() => dismiss(id), tone === "error" ? 9000 : 6000);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast--${t.tone}`}>
            <div className="toast__body">
              <strong>{t.title}</strong>
              {t.lines && t.lines.length > 0 && (
                <ul>
                  {t.lines.map((line, i) => (
                    <li key={i}>{line}</li>
                  ))}
                </ul>
              )}
            </div>
            <button className="toast__close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
