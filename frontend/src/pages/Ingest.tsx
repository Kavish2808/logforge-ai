import { FormEvent, useRef, useState } from "react";
import {
  ArrowRight,
  Braces,
  FileJson2,
  Fingerprint,
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
};

const SAMPLE_COLORS = ["#6cddbb", "#739ef5", "#bc9af1", "#e3b767", "#6cbed4"];

function parseInput(raw: string, mode: string) {
  if (mode === "Single event") return [{ raw }];
  if (mode === "JSON array") {
    const a = JSON.parse(raw);
    if (!Array.isArray(a)) throw new Error("Enter a JSON array of strings or event objects.");
    return a.map((v) => ({ raw: typeof v === "string" ? v : JSON.stringify(v) }));
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
      const events = parseInput(raw, mode);
      if (!events.length) throw new Error("Add at least one event to ingest.");
      const res = await fetch("/api/v1/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, events, idempotency_key: crypto.randomUUID() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || data.detail || `Ingestion failed (${res.status})`);
      setResult(data);
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
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
        <div className="eyebrow">Pipeline input</div>
        <h1>Ingest Logs</h1>
        <p>Submit raw logs through the real pipeline: detection, parsing, normalization, raw preservation and SHA-256 fingerprinting — even when parsing is incomplete.</p>
      </div>
    </div>
    <div className="ingest-layout">
      <div>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>Ingestion workspace</h2>
              <p>Paste a log, upload a file, or submit a complete batch</p>
            </div>
            <span className="secure-label">
              <ShieldCheck size={14} />
              Raw-preserving
            </span>
          </div>

          <form onSubmit={submit} className="ingest-form">
            <div className="form-row">
              <label>
                Source identifier
                <input
                  value={source}
                  required
                  maxLength={120}
                  onChange={(e) => setSource(e.target.value)}
                  placeholder="e.g. production-edge-firewall"
                />
              </label>
              <label>
                Input mode
                <select value={mode} onChange={(e) => setMode(e.target.value)}>
                  {["Single event", "One event per line", "JSON array"].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
            </div>

            <div className="editor-heading">
              <label htmlFor="raw-input">Raw log payload</label>
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
                    if (f.size > 8 * 1024 * 1024) {
                      setError("Select a file smaller than 8 MB.");
                      return;
                    }
                    updateRaw(await f.text());
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
              placeholder={
                "Paste your original log here…\n\nJSON, Syslog, CEF, LEEF, XML, or an unknown format.\nWe preserve the raw evidence even when parsing is incomplete."
              }
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

            {error && (
              <div className="error-notice" role="alert" style={{ marginTop: 12 }}>
                <span>{error}</span>
              </div>
            )}

            <div className="form-bottom">
              <span>
                <Fingerprint size={16} />
                SHA-256 fingerprinting on every event
              </span>
              <button className="button primary" disabled={busy}>
                {busy ? <LoaderCircle size={16} className="spin" /> : <Zap size={16} />}
                Run pipeline
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
