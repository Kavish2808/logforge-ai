import { useEffect, useState } from "react";
import { ApiError, getHealth } from "./api/client";

type HealthState =
  | { kind: "loading" }
  | { kind: "success"; value: string }
  | { kind: "error"; message: string };

export default function App() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    getHealth()
      .then((result) => {
        if (!cancelled) setHealth({ kind: "success", value: result.status });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof ApiError ? err.message : "Backend unreachable";
        setHealth({ kind: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main style={{ fontFamily: "sans-serif", padding: "2rem", maxWidth: 640 }}>
      <h1>LogForge AI</h1>
      <p>Universal Adaptive Log Pre-processing Framework</p>

      <p>
        Backend status:{" "}
        {health.kind === "loading" && <em>checking…</em>}
        {health.kind === "success" && <strong style={{ color: "#1a7f37" }}>{health.value}</strong>}
        {health.kind === "error" && <strong style={{ color: "#cf222e" }}>{health.message}</strong>}
      </p>

      <p style={{ color: "#666" }}>
        Dashboard UI is not implemented yet (planned for a later phase). The
        ingestion API is fully functional — see <code>docs/curl-examples.md</code>{" "}
        or <code>scripts/demo.sh</code> in the repo root.
      </p>
    </main>
  );
}
