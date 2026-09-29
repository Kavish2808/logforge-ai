// Integrations — the registered outbound destinations (SOC_ADMIN) and the alert delivery channels.
// Outbound evidence delivery is NOT implemented by the backend (it answers 501); this page never implies otherwise.
import { FormEvent, useState } from "react";
import { Ban, Cable, CheckCircle2, CircleDashed, Lock, Send } from "lucide-react";
import { createIntegration, getAlertChannels, listIntegrations } from "../api/endpoints";
import { Badge, Card, EmptyState, Load } from "../components/ui";
import { can, useAuth } from "../lib/auth";
import { fmtTime } from "../lib/format";
import { Link } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";

function Registry() {
  const { user } = useAuth();
  const admin = can(user, "governance");
  const items = useApi((s) => (admin ? listIntegrations(s) : Promise.resolve(null)), [admin]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setMsg(null);
    try {
      await createIntegration({ name, url });
      setMsg({ kind: "ok", text: `Registered ${name} (audited). Delivery remains NOT IMPLEMENTED.` });
      setName(""); setUrl("");
      items.reload();
    } catch (err) { setMsg({ kind: "fail", text: errorMessage(err) }); }
  };
  return (
    <Card title={<><Cable size={16} aria-hidden="true" /> Evidence destinations</>}>
      <div className="notice warn" role="note" style={{ marginBottom: 12 }}>
        Outbound evidence delivery is <strong>not implemented</strong>: the deliver endpoint answers <code>501 NOT_IMPLEMENTED</code> and sends nothing.
        Registrations are stored node-locally. Use <Link to="export">Export</Link> to transfer verified evidence to a SIEM.
      </div>
      {!admin ? (
        <EmptyState icon={Lock} title="SOC_ADMIN required">
          The destination registry is readable only by a signed-in SOC_ADMIN. <Link to="login">Sign in</Link>
        </EmptyState>
      ) : (
        <>
          <Load state={items} isEmpty={(d) => !d || d.length === 0} empty="No destinations registered.">
            {(d) => (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Destination</th><th>Endpoint</th><th>Registration</th><th>Delivery</th><th>Last delivery</th><th>Registered</th></tr></thead>
                  <tbody>{(d ?? []).map((i) => (
                    <tr key={i.id}>
                      <td><strong>{i.name}</strong><div className="mono small faint">{i.id}</div></td>
                      <td className="mono small" style={{ overflowWrap: "anywhere" }}>{i.url}</td>
                      <td><Badge value={i.status === "CONFIGURED" ? "CONFIGURED" : i.status} /></td>
                      <td><span className="badge b-neutral" title="The backend does not deliver evidence to external destinations"><Ban size={11} /> {i.delivery.replace(/_/g, " ")}</span></td>
                      <td className="faint small">never — not implemented</td>
                      <td className="small">{fmtTime(i.created_at)}<div className="faint">{i.created_by}</div></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </Load>
          <form className="row" onSubmit={submit} style={{ marginTop: 12 }}>
            <input aria-label="Destination name" placeholder="name, e.g. soc-siem" value={name} onChange={(e) => setName(e.target.value)} />
            <input aria-label="Destination URL" placeholder="https://siem.example.com/ingest" value={url} onChange={(e) => setUrl(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
            <button type="submit" disabled={!name.trim() || !url.trim()}><Send size={14} /> Register</button>
          </form>
          {msg && <div className={`notice ${msg.kind}`} role="status" style={{ marginTop: 8 }}>{msg.text}</div>}
        </>
      )}
    </Card>
  );
}

function Channels() {
  const channels = useApi((s) => getAlertChannels(s), []);
  return (
    <Card title="Alert delivery channels" actions={<Link to="alerts">Alerts →</Link>}>
      <Load state={channels}>
        {(c) => (
          <div className="health-list">
            {c.channels.map((ch) => (
              <div className="health-item" key={ch.channel}>
                <span className="hi-icon" style={{ background: ch.configured ? "var(--ok-bg)" : "var(--neutral-bg)", color: ch.configured ? "var(--ok)" : "var(--neutral)" }}>
                  {ch.configured ? <CheckCircle2 size={15} /> : <CircleDashed size={15} />}
                </span>
                <div><strong style={{ textTransform: "capitalize" }}>{ch.channel}</strong>
                  <div className="hi-meta">{ch.channel === "internal" ? "Internal alert bus" : ch.configured ? "Configured on server" : "Not configured"}</div></div>
                {ch.configured ? <span className="badge b-ok">configured</span> : <span className="badge b-neutral">not configured</span>}
              </div>
            ))}
          </div>
        )}
      </Load>
    </Card>
  );
}

export function IntegrationsPage() {
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Integrations</div>
          <h1>Integrations</h1>
          <p>Outbound SIEM endpoints and alert notification channels.</p>
        </div>
      </div>
      <div className="grid g-main">
        <Registry />
        <Channels />
      </div>
    </>
  );
}
