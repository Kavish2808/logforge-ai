// Fetch hook: loading / error / data with request cancellation and retry.
import { DependencyList, useCallback, useEffect, useState } from "react";
import { ApiError, isAbort } from "../api/client";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function useApi<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: DependencyList): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetcher(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (isAbort(err) || controller.signal.aborted) return;
        setError(errorMessage(err));
        setLoading(false);
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, loading, reload };
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Unexpected error";
}
