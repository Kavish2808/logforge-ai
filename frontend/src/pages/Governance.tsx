// Governance — sign-in, roles (RBAC), review SLAs, runtime configuration and the published RBAC policy.
import { FormEvent, useState } from "react";
import { KeyRound, Lock, ShieldCheck, UserRound, Users as UsersIcon } from "lucide-react";
import {
  bootstrapAdmin, createUser, getAuthStatus, getConfig, getPolicy, getReviews, listUsers, login, logout, putConfig, updateUser,
} from "../api/endpoints";
import { duration } from "../components/trust";
import { Badge, Card, KV, Kpi, Load, Stat } from "../components/ui";
import { can, clearAuth, setAuth, useAuth } from "../lib/auth";
import { num } from "../lib/format";
import { Link } from "../lib/router";
import { errorMessage, useApi } from "../lib/useApi";

const ITEM_LINK: Record<string, string> = { DRIFT_EVENT: "events", ONBOARDING_SESSION: "onboarding", LEARNING_SESSION: "learning" };

function SignIn() {
  const status = useApi((s) => getAuthStatus(s), []);
  const { user } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent, bootstrap: boolean) => {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      if (bootstrap) await bootstrapAdmin(username, password);
      const r = await login(username, password);
      setAuth(r.access_token, { username: r.user.username, role: r.user.role, capabilities: r.user.capabilities });
      setPassword("");
      status.reload();
    } catch (err) {
      setMsg({ kind: "fail", text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  if (user) {
    return (
      <Card title="Signed in">
        <KV items={[["User", user.username], ["Role", <Badge key="r" value={user.role} />], ["Capabilities", user.capabilities.join(", ")]]} />
        <button style={{ marginTop: 10 }} onClick={async () => { try { await logout(); } catch { /* token already invalid */ } clearAuth(); }}>Sign out</button>
      </Card>
    );
  }
  return (
    <Load state={status}>
      {(st) => (
        <Card title={st.bootstrap_required ? "Create the first SOC_ADMIN" : "Sign in"}>
          <p className="small muted" style={{ marginTop: 0 }}>
            RBAC mode <strong>{st.rbac_mode}</strong>: {st.rbac_mode === "enforce"
              ? "governance actions require a signed-in user with the right role."
              : "anonymous actions still work but are audited as anonymous; a signed-in user's role is fully enforced."}
            {" "}Local accounts only (no SSO); passwords are stored as salted PBKDF2 hashes.
          </p>
          {st.production_safe === false && (
            <div className="notice warn" role="note">
              Development / demo mode: governance actions are accepted anonymously and audited as anonymous.
              A production deployment (<code>APP_ENV=production</code>) refuses to start unless <code>RBAC_MODE=enforce</code>.
            </div>
          )}
          <form onSubmit={(e) => submit(e, st.bootstrap_required)} className="row">
            <input aria-label="Username" placeholder="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
            <input aria-label="Password" placeholder="password (12+ characters)" type="password" autoComplete="current-password"
              value={password} onChange={(e) => setPassword(e.target.value)} />
            <button className="primary" type="submit" disabled={busy || !username || !password}>{st.bootstrap_required ? "Create admin & sign in" : "Sign in"}</button>
          </form>
          {msg && <div className={`notice ${msg.kind}`} role="alert" style={{ marginTop: 10 }}>{msg.text}</div>}
        </Card>
      )}
    </Load>
  );
}

function Users() {
  const users = useApi((s) => listUsers(s), []);
  const [form, setForm] = useState({ username: "", password: "", role: "ANALYST" });
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null);
    try { await fn(); setMsg({ kind: "ok", text: ok }); users.reload(); } catch (err) { setMsg({ kind: "fail", text: errorMessage(err) }); }
  };
  return (
    <Card title="Users & roles">
      {msg && <div className={`notice ${msg.kind}`} role="status">{msg.text}</div>}
      <Load state={users}>
        {(d) => (
          <table>
            <thead><tr><th>User</th><th>Role</th><th>Active</th><th /></tr></thead>
            <tbody>{d.items.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>
                  <select aria-label={`Role of ${u.username}`} value={u.role}
                    onChange={(e) => act(() => updateUser(u.username, { role: e.target.value }), `Role of ${u.username} changed (audited).`)}>
                    {["ANALYST", "SECURITY_ENGINEER", "SOC_ADMIN"].map((r) => <option key={r}>{r}</option>)}
                  </select>
                </td>
                <td>{u.active ? "yes" : "no"}</td>
                <td><button className="ghost" onClick={() => act(() => updateUser(u.username, { active: !u.active }), `${u.username} ${u.active ? "deactivated" : "reactivated"}.`)}>
                  {u.active ? "Deactivate" : "Reactivate"}</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Load>
      <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => {
        e.preventDefault();
        act(() => createUser(form), `User ${form.username} created (audited).`).then(() => setForm({ ...form, username: "", password: "" }));
      }}>
        <input aria-label="New username" placeholder="new username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        <input aria-label="New password" placeholder="initial password" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <select aria-label="New role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          {["ANALYST", "SECURITY_ENGINEER", "SOC_ADMIN"].map((r) => <option key={r}>{r}</option>)}
        </select>
        <button type="submit" disabled={!form.username || !form.password}>Add user</button>
      </form>
    </Card>
  );
}

function Reviews() {
  const reviews = useApi((s) => getReviews({ limit: 200 }, s), []);
  return (
    <Card title="Review SLAs" actions={<button onClick={reviews.reload}>Refresh</button>}>
      <Load state={reviews}>
        {(d) => (
          <>
            <div className="stats">
              <Stat accent label="Open reviews" value={num(d.stats.open)} />
              {["PENDING", "DUE_SOON", "OVERDUE", "ESCALATED"].map((k) => <Stat key={k} label={k.replace("_", " ")} value={num(d.stats.by_status[k] ?? 0)} />)}
            </div>
            {d.items.length === 0 ? <div className="state">Nothing is awaiting a human decision.</div> : (
              <div className="table-wrap" style={{ marginTop: 10 }}>
                <table>
                  <thead><tr><th>Item</th><th>Severity</th><th>SLA</th><th>Deadline</th><th>Escalations</th><th>Next action</th></tr></thead>
                  <tbody>{d.items.map((r) => (
                    <tr key={`${r.item_type}:${r.item_id}`}>
                      <td><div className="small muted">{r.item_type.replace(/_/g, " ").toLowerCase()}</div>
                        <Link to={ITEM_LINK[r.item_type]} param={r.item_id} className="mono">{r.source_key ?? r.item_id}</Link></td>
                      <td><Badge value={r.severity} /></td>
                      <td><Badge value={r.status} /></td>
                      <td className="small">{duration(r.seconds_to_deadline)}</td>
                      <td>{r.escalation_count}</td>
                      <td className="small">{r.next_action}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
            <p className="small muted">A review timeout never approves, activates or rejects anything: the known-good adapter and baseline stay in force.</p>
          </>
        )}
      </Load>
    </Card>
  );
}

function Config({ admin }: { admin: boolean }) {
  const config = useApi((s) => getConfig(s), []);
  const [hours, setHours] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null);
  return (
    <Card title="Configuration">
      <Load state={config}>
        {(c) => (
          <>
            <h3>Review SLA (hours by severity)</h3>
            <div className="row">
              {Object.entries(c.review_sla.hours).map(([sev, h]) => (
                <label key={sev} className="field">{sev}
                  <input aria-label={`SLA hours ${sev}`} disabled={!admin} value={hours[sev] ?? String(h)} onChange={(e) => setHours({ ...hours, [sev]: e.target.value })} style={{ width: 80 }} />
                </label>
              ))}
              {admin && <button onClick={async () => {
                setMsg(null);
                try {
                  const body = Object.fromEntries(Object.entries(hours).map(([k, v]) => [k, Number(v)]));
                  await putConfig({ review_sla: { hours: body } });
                  setMsg({ kind: "ok", text: "SLA policy updated (audited). Existing deadlines are durable and unchanged." });
                  setHours({}); config.reload();
                } catch (err) { setMsg({ kind: "fail", text: errorMessage(err) }); }
              }} disabled={!Object.keys(hours).length}>Save</button>}
            </div>
            {!admin && <p className="small faint">Only a SOC_ADMIN can change configuration.</p>}
            {msg && <div className={`notice ${msg.kind}`} role="status">{msg.text}</div>}
            <h3 style={{ marginTop: 14 }}>Alert thresholds</h3>
            <KV items={Object.entries(c.alert_thresholds).map(([k, v]) => [k.replace(/_/g, " "), String(v)])} />
            <h3 style={{ marginTop: 14 }}>Environment (read-only)</h3>
            <KV items={Object.entries(c.static).filter(([, v]) => typeof v !== "object").map(([k, v]) => [k.replace(/_/g, " "), String(v)])} />
          </>
        )}
      </Load>
    </Card>
  );
}

function Policy() {
  const policy = useApi((s) => getPolicy(s), []);
  return (
    <Card title="RBAC policy on governance endpoints">
      <Load state={policy}>
        {(p) => (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Action</th><th>Endpoint</th><th>Maker-checker</th></tr></thead>
              <tbody>{p.rules.map((r) => (
                <tr key={`${r.method} ${r.path}`}>
                  <td className="mono small">{r.action}</td>
                  <td className="mono small">{r.method} {r.path}</td>
                  <td>{r.critical ? <span className="badge b-review">critical · proposer cannot approve</span> : <span className="faint">—</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Load>
    </Card>
  );
}

const ROLE_BLURB: Record<string, string> = {
  ANALYST: "Inspect evidence, review, propose changes and export.",
  SECURITY_ENGINEER: "Approves adapters, drift and learning; performs rollback.",
  SOC_ADMIN: "Owns governance configuration, users and critical approvals.",
};

/** Posture + role hierarchy, straight from GET /auth/status (roles and capabilities are the server's). */
function Posture() {
  const status = useApi((s) => getAuthStatus(s), []);
  const { user } = useAuth();
  return (
    <Load state={status}>
      {(st) => {
        const roles = st.roles ?? Object.keys(st.capabilities ?? {});
        return (
          <>
            <div className="kpis">
              <Kpi label="Current user" value={user ? user.username : "anonymous"} icon={UserRound} tone={user ? "cyan" : undefined}
                hint={user ? "bearer token (session tab only)" : "not signed in"} />
              <Kpi label="Role" value={user ? user.role.replace(/_/g, " ") : "—"} icon={KeyRound} hint={user ? `${user.capabilities.length} capabilities` : "no role"} />
              <Kpi label="Authentication" value={user ? "Authenticated" : "Anonymous"} icon={Lock} tone={user ? "ok" : "warn"} hint="local accounts · PBKDF2" />
              <Kpi label="RBAC mode" value={st.rbac_mode} icon={ShieldCheck} tone={st.rbac_mode === "enforce" ? "ok" : "warn"}
                hint={st.production_safe === false ? "not production-safe" : st.app_env ? `app env: ${st.app_env}` : "server-enforced"} />
              <Kpi label="Maker-checker" value="On" icon={UsersIcon} tone="ok" hint="authors cannot approve critical actions" />
            </div>
            <Card title="Role hierarchy" actions={<span className="small faint">each role holds every capability of the roles below it</span>}>
              <div className="roles">
                {roles.map((r, i) => {
                  const caps = st.capabilities?.[r] ?? [];
                  const below = i > 0 ? st.capabilities?.[roles[i - 1]] ?? [] : [];
                  return (
                    <div key={r} className={`role-card${user?.role === r ? " current" : ""}`}>
                      <div className="role-rank">LEVEL {String(i + 1).padStart(2, "0")}{user?.role === r ? " · YOU" : ""}</div>
                      <h4><Badge value={r} /></h4>
                      <p className="small" style={{ marginBottom: 10 }}>{ROLE_BLURB[r] ?? ""}</p>
                      <ul aria-label={`${r} capabilities`}>
                        {caps.map((c) => <li key={c} className={below.includes(c) ? "inherited" : ""} title={below.includes(c) ? "inherited" : "granted at this level"}>{c}</li>)}
                      </ul>
                    </div>
                  );
                })}
              </div>
            </Card>
          </>
        );
      }}
    </Load>
  );
}

export function GovernancePage() {
  const { user } = useAuth();
  const admin = can(user, "manage_roles");
  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Trust · administration</div>
          <h1>Governance</h1>
          <p>Roles (ANALYST · SECURITY_ENGINEER · SOC_ADMIN), maker-checker on critical approvals, review SLAs and runtime configuration — every change audited.</p>
        </div>
        <Link to="audit">Audit log →</Link>
      </div>
      <Posture />
      <div className="grid g2">
        <SignIn />
        {admin ? <Users /> : (
          <Card title="Roles">
            <KV items={[
              ["ANALYST", "inspect, review, propose"],
              ["SECURITY_ENGINEER", "approve/reject adapters, drift and learning; rollback"],
              ["SOC_ADMIN", "governance/configuration, role management, critical approvals"],
            ]} />
          </Card>
        )}
      </div>
      <div style={{ marginTop: 16 }}><Reviews /></div>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <Config admin={admin} />
        <Policy />
      </div>
    </>
  );
}
