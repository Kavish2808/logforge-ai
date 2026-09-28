import { useEffect, useState } from "react";
import { getAlertCounts, getHealth, getSummary } from "./api/endpoints";
import { Quiet } from "./components/trust";
import { useAuth } from "./lib/auth";
import { useApi } from "./lib/useApi";
import { href, useRoute } from "./lib/router";
import { AdapterEvolution } from "./pages/AdapterEvolution";
import { AdvancedDrift } from "./pages/AdvancedDrift";
import { Baselines } from "./pages/Baselines";
import { Correlations } from "./pages/Correlations";
import { ReplayPage } from "./pages/Replay";
import { AlertsPage } from "./pages/Alerts";
import { AuditPage } from "./pages/Audit";
import { Demo } from "./pages/Demo";
import { DriftQueue } from "./pages/DriftQueue";
import { EventExplorer } from "./pages/EventExplorer";
import { EventForensics } from "./pages/EventForensics";
import { ExportPage } from "./pages/Export";
import { GovernancePage } from "./pages/Governance";
import { IntegrityPage } from "./pages/Integrity";
import { Learning } from "./pages/Learning";
import { Onboarding } from "./pages/Onboarding";
import { Overview } from "./pages/Overview";
import { Sources } from "./pages/Sources";

const NAV: { group: string; items: { id: string; label: string }[] }[] = [
  { group: "Operate", items: [{ id: "overview", label: "Overview" }, { id: "events", label: "Event Explorer" }, { id: "sources", label: "Sources" }] },
  { group: "Adapt", items: [{ id: "drift", label: "Drift Queue" }, { id: "advanced-drift", label: "Advanced Drift" }, { id: "baselines", label: "Baseline Integrity" },
    { id: "onboarding", label: "Onboarding" }, { id: "evolution", label: "Adapter Evolution" }, { id: "learning", label: "Learning" }] },
  { group: "Investigate", items: [{ id: "correlations", label: "Correlations" }, { id: "replay", label: "Replay & Revisions" }] },
  { group: "Trust", items: [{ id: "integrity", label: "Integrity" }, { id: "alerts", label: "Alerts" }, { id: "audit", label: "Audit Log" }, { id: "governance", label: "Governance" }] },
  { group: "Integrate", items: [{ id: "export", label: "Export" }] },
  { group: "Showcase", items: [{ id: "demo", label: "Demo" }] },
];

function HealthIndicator() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, []);
  const health = useApi((s) => getHealth(s), [tick]);
  const ok = health.data?.status === "ok" && !health.error;
  return (
    <div className="nav-footer" aria-live="polite">
      <span className={`dot ${health.loading && !health.data ? "" : ok ? "ok" : "fail"}`} aria-hidden="true" />
      {health.loading && !health.data ? "Checking API…" : ok ? "API + database healthy" : "API unreachable"}
    </div>
  );
}

function UnreadAlerts() {
  const s = useApi((sig) => getAlertCounts(sig), []);
  return <Quiet state={s}>{(c) => c.unread ? <span className="badge b-fail" aria-label={`${c.unread} unread alerts`}>{c.unread}</span> : null}</Quiet>;
}

function Identity() {
  const { user } = useAuth();
  return (
    <div className="nav-footer small">
      {user ? <>Signed in: <strong>{user.username}</strong> · {user.role.replace(/_/g, " ")}</> : <a href={href("governance")}>Sign in (governance)</a>}
    </div>
  );
}

function PendingCount() {
  const s = useApi((sig) => getSummary({}, sig), []);
  const n = s.data?.totals.pending_reviews;
  return n ? <span className="badge b-review" aria-label={`${n} pending reviews`}>{n}</span> : null;
}

export default function App() {
  const route = useRoute();
  const section = route.section === "events" && route.param ? "forensics" : route.section;
  const page = (() => {
    switch (section) {
      case "events": return <EventExplorer />;
      case "forensics": return <EventForensics id={route.param!} />;
      case "sources": return <Sources sourceKey={route.param} />;
      case "drift": return <DriftQueue />;
      case "advanced-drift": return <AdvancedDrift />;
      case "baselines": return <Baselines sourceKey={route.param} />;
      case "correlations": return <Correlations correlationId={route.param} />;
      case "replay": return <ReplayPage jobId={route.param} />;
      case "onboarding": return <Onboarding sessionId={route.param} />;
      case "evolution": return <AdapterEvolution sourceKey={route.param} />;
      case "learning": return <Learning sessionId={route.param} />;
      case "export": return <ExportPage />;
      case "integrity": return <IntegrityPage />;
      case "alerts": return <AlertsPage />;
      case "audit": return <AuditPage />;
      case "governance": return <GovernancePage />;
      case "demo": return <Demo />;
      default: return <Overview />;
    }
  })();
  const activeNav = section === "forensics" ? "events" : section;
  return (
    <div className="shell">
      <nav className="nav" aria-label="Primary">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">LF</div>
          <div>
            <div className="brand-name">LogForge AI</div>
            <div className="brand-sub">Adaptive log intelligence</div>
          </div>
        </div>
        {NAV.map((g) => (
          <div key={g.group} style={{ display: "contents" }}>
            <div className="nav-group">{g.group}</div>
            {g.items.map((item) => (
              <a key={item.id} href={href(item.id)} className={activeNav === item.id ? "active" : ""}
                aria-current={activeNav === item.id ? "page" : undefined}>
                {item.label}
                {item.id === "drift" && <PendingCount />}
                {item.id === "alerts" && <UnreadAlerts />}
              </a>
            ))}
          </div>
        ))}
        <Identity />
        <HealthIndicator />
      </nav>
      <main className="main" id="main">{page}</main>
    </div>
  );
}
