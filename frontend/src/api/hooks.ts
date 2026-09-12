/**
 * Two hooks, no data-fetching library.
 *
 * `useAsync` loads once and re-runs when its key changes. `usePolling` adds an interval,
 * which the ops console needs because a support tool showing a five-minute-old service
 * status is worse than showing none. Both keep the previous value visible while
 * refreshing, so a live panel does not blink to a spinner every few seconds.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  /** True only for the very first load, so panels can skeleton once and never again. */
  initial: boolean;
  refresh: () => void;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const [initial, setInitial] = useState(true);
  const [tick, setTick] = useState(0);

  // The caller passes a fresh closure every render; pinning it in a ref keeps it out of
  // the effect's dependency list without lying to the linter about what it uses.
  const latest = useRef(fn);
  latest.current = fn;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    latest
      .current()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(undefined);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
        setInitial(false);
      });
    return () => {
      // Guards against a slow response from a previous key overwriting a newer one.
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const refresh = useCallback(() => setTick((value) => value + 1), []);
  return { data, error, loading, initial, refresh };
}

export function usePolling<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = [],
): AsyncState<T> & { paused: boolean; setPaused: (value: boolean) => void } {
  const [paused, setPaused] = useState(false);
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  const state = useAsync(fn, deps);
  const refresh = state.refresh;

  // A background tab polling a support API every two seconds is pure waste, so the
  // interval stops with the tab and takes one fresh reading when it comes back.
  useEffect(() => {
    const onVisibility = () => {
      const now = document.visibilityState === "visible";
      setVisible(now);
      if (now) refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [refresh]);

  useEffect(() => {
    if (paused || !visible || intervalMs <= 0) return;
    const handle = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(handle);
  }, [paused, visible, intervalMs, refresh]);

  return { ...state, paused, setPaused };
}
