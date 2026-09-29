// Sign-in landing. Uses the existing auth contract only: GET /auth/status, POST /auth/bootstrap (the server
// refuses it once any user exists) and POST /auth/login. Roles are assigned by a SOC_ADMIN, never chosen here.
import { FormEvent, useState } from "react";
import { ArrowRight, Braces, Brain, Database, Fingerprint, KeyRound, Layers, LoaderCircle, Lock, Moon, Radar, ShieldCheck, Sun } from "lucide-react";
import { bootstrapAdmin, getAuthStatus, login } from "../api/endpoints";
import { setAuth, useAuth } from "../lib/auth";
import { errorMessage, useApi } from "../lib/useApi";
import { useTheme } from "../lib/theme";

const FLOW = [
  { icon: Radar, name: "Detect", text: "Format identification" },
  { icon: Braces, name: "Parse", text: "Syslog · JSON · CEF · LEEF · XML" },
  { icon: Layers, name: "Normalize", text: "OCSF schema alignment" },
  { icon: Database, name: "Preserve", text: "Immutable raw byte vault" },
  { icon: Fingerprint, name: "Verify", text: "SHA-256 Merkle proofs" },
  { icon: Brain, name: "Learn", text: "Human-in-the-loop drift" },
];

export function LoginPage({ onEnter }: { onEnter: () => void }) {
  const status = useApi((s) => getAuthStatus(s), []);
  const { user } = useAuth();
  const { theme, toggle: toggleTheme } = useTheme();
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
    <div className="login" style={{ position: "relative" }}>
      <div style={{ position: "absolute", top: 16, right: 16, zIndex: 10 }}>
        <button
          type="button"
          className="icon-button"
          title={theme === "dark" ? "Switch to white background" : "Switch to dark background"}
          aria-label="Toggle background theme"
          onClick={toggleTheme}
        >
          {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>
      <section className="login-story" aria-label="LogForge AI">
        <div className="login-brand">
          <svg width="34" height="34" viewBox="0 0 40 40" aria-hidden="true" style={{ borderRadius: 9, flexShrink: 0, boxShadow: "0 2px 6px rgba(0,0,0,0.18)" }}>
            <rect width="40" height="40" rx="11" fill="#ffffff" />
            <path d="M10 10h9v5h-4v10h10v-4h5v9H10zm12 0h8v8h-5v-3h-3z" fill="#090b10" />
          </svg>
          <div>
            <div className="bn">LOGFORGE</div>
            <div className="bs">Platform Console</div>
          </div>
        </div>
        <div className="login-hero">
          <div className="eyebrow">Security Evidence</div>
          <h1>Raw logs to <span>verifiable evidence.</span></h1>
          <p>Deterministic parsing, OCSF normalization, cryptographic WORM vaulting, and drift governance.</p>
        </div>
        <ol className="login-flow" aria-label="Pipeline">
          {FLOW.map(({ icon: Icon, name, text }) => (
            <li key={name}><Icon size={17} aria-hidden="true" /><div><strong>{name}</strong><span>{text}</span></div></li>
          ))}
        </ol>
        <div className="login-foot">
          <span><Lock size={12} /> SHA-256 tokens</span>
          <span><ShieldCheck size={12} /> Hash-chained audit</span>
          <span><KeyRound size={12} /> Local PBKDF2</span>
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
              <h2>{bootstrap ? "Initialize SOC_ADMIN" : "Sign in"}</h2>
              <p className="sub">
                {status.loading && !status.data ? "Checking auth…"
                  : bootstrap ? "First account initializes SOC_ADMIN."
                  : "Enter your LogForge credentials."}
              </p>
              {status.error && <div className="notice fail" role="alert" style={{ marginBottom: 14 }}>Auth service unavailable: {status.error}</div>}
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
                  RBAC: <strong>{status.data.rbac_mode}</strong>
                  {status.data.rbac_mode === "enforce"
                    ? " — Authentication enforced."
                    : " — Permissive mode active."}
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
