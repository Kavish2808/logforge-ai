// Sign-in landing. Uses the existing auth contract only: GET /auth/status, POST /auth/bootstrap (the server
// refuses it once any user exists) and POST /auth/login. Roles are assigned by a SOC_ADMIN, never chosen here.
import { FormEvent, useState } from "react";
import { ArrowRight, Braces, Brain, Database, Fingerprint, Hexagon, KeyRound, Layers, LoaderCircle, Lock, Radar, ShieldCheck } from "lucide-react";
import { bootstrapAdmin, getAuthStatus, login } from "../api/endpoints";
import { setAuth, useAuth } from "../lib/auth";
import { errorMessage, useApi } from "../lib/useApi";

const FLOW = [
  { icon: Radar, name: "Detect", text: "Format & vendor fingerprinting" },
  { icon: Braces, name: "Parse", text: "Syslog · JSON · CEF · LEEF · XML" },
  { icon: Layers, name: "Normalize", text: "OCSF-aligned universal schema" },
  { icon: Database, name: "Preserve", text: "Raw bytes kept, hot & cold" },
  { icon: Fingerprint, name: "Verify", text: "SHA-256 + Merkle evidence chain" },
  { icon: Brain, name: "Learn", text: "Drift-driven, human-approved" },
];

export function LoginPage({ onEnter }: { onEnter: () => void }) {
  const status = useApi((s) => getAuthStatus(s), []);
  const { user } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bootstrap = status.data?.bootstrap_required === true;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (bootstrap) await bootstrapAdmin(username, password);
      const r = await login(username, password);
      setAuth(r.access_token, { username: r.user.username, role: r.user.role, capabilities: r.user.capabilities });
      setPassword("");
      onEnter();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <section className="login-story" aria-label="LogForge AI">
        <div className="login-brand">
          <div className="brand-mark" aria-hidden="true"><Hexagon size={22} strokeWidth={2.4} /></div>
          <div>
            <div className="bn">LOGFORGE <span style={{ color: "var(--cyan)" }}>AI</span></div>
            <div className="bs">Universal Adaptive Log Pre-processing Framework</div>
          </div>
        </div>
        <div className="login-hero">
          <div className="eyebrow">Security evidence platform</div>
          <h1>From raw logs to <span>trusted, adaptive evidence.</span></h1>
          <p>Any log is detected, parsed and normalized, its raw bytes preserved and cryptographically verified, and its parsers adapt to change under evidence and human approval.</p>
        </div>
        <ol className="login-flow" aria-label="Pipeline">
          {FLOW.map(({ icon: Icon, name, text }) => (
            <li key={name}><Icon size={17} aria-hidden="true" /><div><strong>{name}</strong><span>{text}</span></div></li>
          ))}
        </ol>
        <div className="login-foot">
          <span><Lock size={12} /> Tokens stored as SHA-256 hashes</span>
          <span><ShieldCheck size={12} /> Hash-chained audit of every decision</span>
          <span><KeyRound size={12} /> Local accounts · PBKDF2</span>
        </div>
      </section>

      <section className="login-form-side">
        <div className="login-card">
          {user ? (
            <>
              <h2>Signed in as {user.username}</h2>
              <p className="sub">{user.role.replace(/_/g, " ")} · {user.capabilities.join(", ")}</p>
              <button className="primary" type="button" onClick={onEnter} style={{ width: "100%" }}>Open console <ArrowRight size={15} /></button>
            </>
          ) : (
            <>
              <h2>{bootstrap ? "Create the first SOC_ADMIN" : "Sign in"}</h2>
              <p className="sub">
                {status.loading && !status.data ? "Checking authentication status…"
                  : bootstrap ? "No account exists yet. The first account becomes SOC_ADMIN; the server refuses this once any user exists."
                  : "Use your LogForge account. Roles are assigned by a SOC_ADMIN."}
              </p>
              {status.error && <div className="notice fail" role="alert" style={{ marginBottom: 14 }}>Authentication service unavailable: {status.error}</div>}
              <form onSubmit={submit}>
                <label>Username
                  <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required autoFocus />
                </label>
                <label>Password
                  <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                    autoComplete={bootstrap ? "new-password" : "current-password"} placeholder={bootstrap ? "12+ characters" : undefined} required />
                </label>
                {error && <div className="notice fail" role="alert">{error}</div>}
                <button className="primary" type="submit" disabled={busy || !username || !password || !status.data}>
                  {busy ? <LoaderCircle size={16} className="spin" /> : <KeyRound size={15} />}
                  {bootstrap ? "Create admin & sign in" : "Sign in"}
                </button>
              </form>
              {status.data && (
                <div className="mode-note">
                  RBAC mode <strong>{status.data.rbac_mode}</strong>
                  {status.data.rbac_mode === "enforce"
                    ? " — governed actions, ingestion and export require a signed-in role."
                    : " — development / demo: anonymous use is allowed and audited as anonymous."}
                  {status.data.rbac_mode !== "enforce" && (
                    <>
                      <div className="divider">or</div>
                      <button type="button" onClick={onEnter} style={{ width: "100%" }}>Continue without signing in</button>
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
