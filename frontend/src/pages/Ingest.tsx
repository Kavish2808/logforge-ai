import { FormEvent, useRef, useState } from "react";
import {
  ArrowRight,
  Braces,
  FileJson2,
  Fingerprint,
  Flame,
  LoaderCircle,
  Plus,
  ShieldCheck,
  Sparkles,
  Upload,
  Zap,
} from "lucide-react";
import { href } from "../lib/router";

const SAMPLES: Record<string, string> = {
  JSON: '{"timestamp":"2026-09-28T09:30:00Z","vendor":"custom","src_ip":"10.20.0.12","dst_ip":"203.0.113.24","dst_port":443,"action":"allow","policy_id":"edge-17","bytes":2048}',
  Syslog:
    "<134>Sep 28 09:30:00 edge-fw %ASA-6-302013: Built outbound TCP connection 123 for outside:203.0.113.24/443 to inside:10.20.0.12/51822",
  CEF: "CEF:0|Example|Firewall|1.0|100|Connection allowed|3|src=10.20.0.12 dst=203.0.113.24 dpt=443 act=allow custom_field=preserved",
  LEEF: "LEEF:1.0|Example|Gateway|1.0|100|src=10.20.0.12\tdst=203.0.113.24\tdstPort=443\taction=allow",
  XML: "<event><vendor>Example</vendor><src_ip>10.20.0.12</src_ip><dst_ip>203.0.113.24</dst_ip><action>allow</action><policy_id>edge-17</policy_id></event>",
  CSV: "timestamp,src_ip,dst_ip,proto,action\n2026-09-30T00:00:00Z,10.0.1.15,203.0.113.25,TCP,accept\n2026-09-30T00:00:01Z,10.0.1.18,203.0.113.88,UDP,drop",
};

const SAMPLE_COLORS = ["#6cddbb", "#739ef5", "#bc9af1", "#e3b767", "#6cbed4", "#ec7995"];

function parseInput(raw: string, mode: string) {
  if (mode === "Single event") {
    // If the input is clearly multi-line CSV or logs, automatically split to prevent single event byte overflow
    if (raw.includes("\n") && (raw.includes(",") || raw.includes(" "))) {
      const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
      if (lines.length > 1) {
        return lines.map((v) => ({ raw: v }));
      }
    }
    return [{ raw }];
  }
  if (mode === "JSON array") {
    const a = JSON.parse(raw);
    if (!Array.isArray(a)) throw new Error("Enter a JSON array of strings or event objects.");
    return a.map((v) => ({ raw: typeof v === "string" ? v : JSON.stringify(v) }));
  }
  if (mode === "CSV / Tabular rows") {
    const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    if (lines.length > 1 && lines[0].includes(",")) {
      const headers = lines[0].split(",").map((h) => h.trim().replace(/^["']|["']$/g, ""));
      return lines.slice(1).map((line) => {
        const parts = line.split(",").map((p) => p.trim().replace(/^["']|["']$/g, ""));
        const obj: Record<string, any> = {};
        headers.forEach((h, i) => {
          obj[h] = parts[i] !== undefined ? parts[i] : "";
        });
        return { raw: JSON.stringify(obj) };
      });
    }
  }
  return raw
    .split(/\r?\n/)
    .filter((v) => v.trim())
    .map((v) => ({ raw: v }));
}

export function IngestPage() {
  const [source, setSource] = useState("");
  const [raw, setRaw] = useState("");
  const [mode, setMode] = useState("Single event");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [progressText, setProgressText] = useState<string | null>(null);
  const [progressPct, setProgressPct] = useState<number | null>(null);
  const file = useRef<HTMLInputElement>(null);

  function updateRaw(v: string) {
    setRaw(v);
    setError("");
    setResult(undefined);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      let allEvents: { raw: string }[] = [];
      if (selectedFile) {
        setProgressText(`Reading ${selectedFile.name} (${(selectedFile.size / (1024 * 1024)).toFixed(1)} MB)…`);
        setProgressPct(0);
        const text = await selectedFile.text();
        const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 0 && !l.startsWith("# --- Previewing"));
        if (!lines.length) throw new Error("No valid log lines found in selected file.");
        allEvents = lines.map((l) => ({ raw: l }));
      } else {
        allEvents = parseInput(raw, mode);
      }

      if (!allEvents.length) throw new Error("Add at least one event to ingest.");

      // Stream any batch with > 50 events in micro-batches to give live progress and avoid payload limits
      if (allEvents.length > 50) {
        const chunkSize = 1000;
        let totalAccepted = 0;
        let sCount = 0;
        let pCount = 0;
        let fCount = 0;
        let sampleResults: any[] = [];

        for (let i = 0; i < allEvents.length; i += chunkSize) {
          const chunk = allEvents.slice(i, i + chunkSize);
          const pct = Math.round((i / allEvents.length) * 100);
          setProgressPct(pct);
          setProgressText(`Streaming: ${i.toLocaleString()} / ${allEvents.length.toLocaleString()} logs (${pct}%)…`);

          const res = await fetch("/api/v1/ingest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: source || "default",
              events: chunk,
              idempotency_key: crypto.randomUUID(),
            }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.message || data.error?.message || `Chunk failed at log ${i}`);
          totalAccepted += data.accepted || chunk.length;
          sCount += data.success_count || 0;
          pCount += data.partial_count || 0;
          fCount += data.failed_count || 0;
          if (sampleResults.length < 10 && data.results) {
            sampleResults.push(...data.results);
          }
        }

        setProgressPct(100);
        setProgressText(null);
        setResult({
          accepted: totalAccepted,
          duplicates: 0,
          total: allEvents.length,
          success_count: sCount,
          partial_count: pCount,
          failed_count: fCount,
          results: sampleResults,
        });
        return;
      }

      // Single small payload (<= 50 events)
      const res = await fetch("/api/v1/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: source || "default", events: allEvents, idempotency_key: crypto.randomUUID() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        let msg = "";
        if (data.error) {
          msg = data.error.message || "";
          if (data.error.fields) {
            const fieldDetails = Object.entries(data.error.fields)
              .map(([f, m]) => `${f ? f + ": " : ""}${m}`)
              .join("; ");
            msg = msg ? `${msg}: ${fieldDetails}` : fieldDetails;
          }
        } else if (Array.isArray(data.detail)) {
          msg = data.detail.map((d: any) => `${d.loc ? d.loc.slice(1).join(".") + ": " : ""}${d.msg}`).join("; ");
        } else if (typeof data.detail === "string") {
          msg = data.detail;
        } else if (typeof data.message === "string") {
          msg = data.message;
        }
        throw new Error(msg || `Ingestion failed (${res.status})`);
      }
      setResult(data);
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
      setProgressText(null);
      setProgressPct(null);
    }
  }

  async function loadDemo() {
    setError("");
    setBusy(true);
    try {
      const res = await fetch("/api/v1/ingest/demo", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || data.detail || `Demo ingestion failed (${res.status})`);
      setResult(data);
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    <div className="page-head">
      <div>
        <div className="eyebrow">Data Ingestion</div>
        <h1>Ingest Logs</h1>
        <p>Submit raw logs into the parsing, normalization, and cryptographic vault pipeline.</p>
      </div>
      <div className="row">
        <a href="#/devices" className="button small" style={{ gap: 6, fontWeight: 600 }}>
          <Flame size={14} style={{ color: "#f97316" }} /> 1M Scale Engine →
        </a>
      </div>
    </div>
    <div className="ingest-layout">
      <div>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Ingestion Console</h2>
              <p>Direct entry, file upload, or batch payload</p>
            </div>
            <span className="secure-label">
              <ShieldCheck size={14} />
              Raw-preserving
            </span>
          </div>

          <form onSubmit={submit} className="ingest-form">
            <div className="form-row">
              <label>
                Source key
                <input
                  value={source}
                  required
                  maxLength={120}
                  onChange={(e) => setSource(e.target.value)}
                  placeholder="e.g. edge-firewall"
                />
              </label>
              <label>
                Input mode
                <select value={mode} onChange={(e) => setMode(e.target.value)}>
                  {["Single event", "One event per line", "CSV / Tabular rows", "JSON array"].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
            </div>

            <div className="editor-heading">
              <label htmlFor="raw-input">Raw payload</label>
              <button type="button" className="text-button" onClick={() => file.current?.click()}>
                <Upload size={14} />
                Upload file
              </button>
              <input
                ref={file}
                type="file"
                accept=".log,.txt,.json,.ndjson,.xml,.csv"
                hidden
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  if (f) {
                    if (f.size > 250 * 1024 * 1024) {
                      setError("Select a file smaller than 250 MB.");
                      return;
                    }
                    setSelectedFile(f);
                    if (f.size <= 2 * 1024 * 1024) {
                      const text = await f.text();
                      updateRaw(text);
                      if (f.name.toLowerCase().endsWith(".csv") || text.split("\n")[0]?.includes(",")) {
                        setMode("CSV / Tabular rows");
                      } else if (text.trim().startsWith("[") && text.trim().endsWith("]")) {
                        setMode("JSON array");
                      } else if (text.includes("\n")) {
                        setMode("One event per line");
                      }
                    } else {
                      const slice = f.slice(0, 128 * 1024);
                      const text = await slice.text();
                      const lines = text.split("\n").slice(0, 50).join("\n");
                      updateRaw(`${lines}\n\n# --- Previewing first 50 lines of ${f.name} (${(f.size / (1024 * 1024)).toFixed(1)} MB). Full file will be streamed during ingest. ---`);
                      if (f.name.toLowerCase().endsWith(".csv")) {
                        setMode("CSV / Tabular rows");
                      } else {
                        setMode("One event per line");
                      }
                    }
                  }
                  e.target.value = "";
                }}
              />
            </div>

            <textarea
              id="raw-input"
              className="log-editor"
              value={raw}
              onChange={(e) => updateRaw(e.target.value)}
              placeholder="Paste raw log payload (JSON, Syslog, CEF, LEEF, XML). Raw bytes are preserved verbatim."
              required
              spellCheck={false}
            />

            <div className="editor-footer">
              <span>
                <Braces size={14} />
                {new TextEncoder().encode(raw).length.toLocaleString()} bytes
              </span>
              <span>UTF-8 input</span>
            </div>

            {selectedFile && (
              <div className="small faint" style={{ marginTop: 8, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span>Selected file: <strong>{selectedFile.name}</strong> ({(selectedFile.size / (1024 * 1024)).toFixed(1)} MB)</span>
                <button type="button" className="text-button small" onClick={() => { setSelectedFile(null); updateRaw(""); }} style={{ padding: 0 }}>Remove file</button>
              </div>
            )}

            {error && (
              <div className="error-notice" role="alert" style={{ marginTop: 12 }}>
                <span>{error}</span>
              </div>
            )}

            {progressText && (
              <div style={{ marginTop: 12, padding: "10px 14px", background: "rgba(16, 185, 129, 0.08)", border: "1px solid rgba(16, 185, 129, 0.3)", borderRadius: 8 }}>
                <div className="row spread small" style={{ marginBottom: 6 }}>
                  <span style={{ fontWeight: 600, color: "var(--ok)" }}>{progressText}</span>
                  {progressPct !== null && <span className="mono" style={{ fontWeight: 700 }}>{progressPct}%</span>}
                </div>
                <div style={{ width: "100%", height: 6, background: "rgba(255,255,255,0.1)", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{ width: `${progressPct ?? 100}%`, height: "100%", background: "linear-gradient(90deg, #10b981, #6366f1)", transition: "width 0.15s ease" }} />
                </div>
              </div>
            )}

            <div className="form-bottom">
              <span>
                <Fingerprint size={16} />
                SHA-256 fingerprinting on every event
              </span>
              <button className="button primary" disabled={busy}>
                {busy ? <LoaderCircle size={16} className="spin" /> : <Zap size={16} />}
                {progressText ? "Streaming…" : "Run pipeline"}
              </button>
            </div>
          </form>
        </section>

        {result && (
          <section className="panel result-panel" style={{ marginTop: 20 }}>
            <div className="panel-heading">
              <div>
                <h2>Batch received</h2>
                <p>Authoritative ingestion result</p>
              </div>
            </div>
            <div className="result-stats">
              <div>
                <strong>{(result.accepted ?? result.total ?? 1).toLocaleString()}</strong>
                <span>Accepted</span>
              </div>
              <div>
                <strong>{(result.duplicates ?? 0).toLocaleString()}</strong>
                <span>Duplicates</span>
              </div>
              <a className="button secondary small" href={href("events")}>
                Explore evidence <ArrowRight size={14} />
              </a>
            </div>
            <details className="result-details" open>
              <summary>Batch and Merkle evidence</summary>
              <pre className="json">{JSON.stringify(result, null, 2)}</pre>
            </details>
          </section>
        )}
      </div>

      <div className="ingest-aside">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Start with a sample</h2>
              <p>Insert a payload into the editor</p>
            </div>
          </div>
          <div className="sample-list">
            {Object.keys(SAMPLES).map((s, i) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  updateRaw(SAMPLES[s]);
                  setMode("Single event");
                  if (!source) setSource("sample-source");
                }}
              >
                <span className="sample-icon" style={{ color: SAMPLE_COLORS[i] }}>
                  <FileJson2 size={19} />
                </span>
                <div>
                  <strong>{s}</strong>
                  <span>
                    {[
                      "Structured event data",
                      "Network & system messages",
                      "Common Event Format",
                      "Log Event Extended Format",
                      "Structured XML events",
                    ][i]}
                  </span>
                </div>
                <Plus size={16} />
              </button>
            ))}
          </div>
        </section>

        <div className="demo-card">
          <Sparkles size={23} />
          <h3>See the complete picture.</h3>
          <p>Load a labeled sample dataset to explore multiple vendors, parsing outcomes, and drift signals.</p>
          <button className="button secondary" disabled={busy} type="button" onClick={loadDemo}>
            Load sample dataset <ArrowRight size={14} />
          </button>
          <small>Creates real sample records across Fortinet, Cisco, CEF, and LEEF.</small>
        </div>
      </div>
    </div>
    </>
  );
}
