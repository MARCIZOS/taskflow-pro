import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { api, ApiError } from "./api";
import { useToast } from "./components/Toasts";
import { shortDate } from "./lib";
import type { Board, MutationResult } from "./types";

export const BOARD_KEY = ["board"] as const;
export const SUGGESTIONS_KEY = ["suggestions"] as const;
export const METRICS_KEY = ["ai-metrics"] as const;

export function useBoard() {
  return useQuery({ queryKey: BOARD_KEY, queryFn: api.board });
}

/**
 * Wraps a mutation call: on success the server's board replaces the cache and
 * a toast explains what happened (including tasks the engine moved); on
 * failure the board is re-fetched and the server's message is shown.
 */
export function useRunMutation() {
  const qc = useQueryClient();
  const toast = useToast();

  return useCallback(
    async (call: () => Promise<MutationResult>, successTitle?: string): Promise<MutationResult | null> => {
      try {
        const result = await call();
        qc.setQueryData<Board>(BOARD_KEY, result.board);
        const all = result.schedule_changes.map(
          (c) =>
            `${c.title}: ${shortDate(c.old_start)} → ${shortDate(c.new_start)} (${c.shift_days > 0 ? "+" : ""}${c.shift_days} day${Math.abs(c.shift_days) === 1 ? "" : "s"})`,
        );
        // Keep the toast short; the board itself shows every new date.
        const lines = all.length > 5 ? [...all.slice(0, 4), `…and ${all.length - 4} more`] : all;
        if (result.newly_blocked.length) {
          toast.show("info", result.message || "Some tasks are blocked again", lines);
        } else if (successTitle || result.message || lines.length) {
          toast.show("success", result.message || successTitle || "Saved", lines);
        }
        return result;
      } catch (err) {
        reportError(toast, err);
        await qc.invalidateQueries({ queryKey: BOARD_KEY });
        return null;
      }
    },
    [qc, toast],
  );
}

export function reportError(toast: ReturnType<typeof useToast>, err: unknown) {
  if (err instanceof ApiError) {
    const title =
      err.code === "cycle" ? "Dependency rejected" : err.code === "blocked" ? "Task is blocked" : "Couldn't save that";
    toast.show("error", title, [err.message]);
  } else {
    toast.show("error", "Something went wrong", [String(err)]);
  }
}
