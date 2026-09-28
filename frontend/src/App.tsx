import { useEffect, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  Bell,
  Database,
  FileText,
  Fingerprint,
  GitBranch,
  Layers3,
  LayoutDashboard,
  LucideIcon,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Upload,
  Workflow,
} from "lucide-react";
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
import { IngestPage } from "./pages/Ingest";
import { IntegrityPage } from "./pages/Integrity";
import { Learning } from "./pages/Learning";
import { Onboarding } from "./pages/Onboarding";
import { Overview } from "./pages/Overview";
import { Sources } from "./pages/Sources";

interface NavItem {
  id: string;
  label: string;
  icon: LucideIcon;
}

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Operate",
    items: [
      { id: "overview", label: "Overview", icon: LayoutDashboard },
      { id: "ingest", label: "Ingest Logs", icon: Upload },
      { id: "events", label: "Event Explorer", icon: Layers3 },
      { id: "sources", label: "Sources", icon: Database },
    ],
  },
  {
    group: "Adapt",
    items: [
      { id: "drift", label: "Drift Queue", icon: Activity },
      { id: "advanced-drift", label: "Advanced Drift", icon: Workflow },
      { id: "baselines", label: "Baseline Integrity", icon: ShieldCheck },
      { id: "onboarding", label: "Onboarding", icon: Sparkles },
      { id: "evolution", label: "Adapter Evolution", icon: GitBranch },
      { id: "learning", label: "Learning", icon: Sparkles },
    ],
  },
  {
    group: "Investigate",
    items: [
      { id: "correlations", label: "Correlations", icon: Radio },
      { id: "replay", label: "Replay & Revisions", icon: RotateCcw },
    ],
  },
  {
    group: "Trust",
    items: [
      { id: "integrity", label: "Integrity", icon: Fingerprint },
      { id: "alerts", label: "Alerts", icon: Bell },
      { id: "audit", label: "Audit Log", icon: FileText },
      { id: "governance", label: "Governance", icon: ShieldCheck },
    ],
  },
  {
    group: "Integrate",
    items: [{ id: "export", label: "Export", icon: ArrowDownToLine }],
  },
  {
    group: "Showcase",
    items: [{ id: "demo", label: "Demo", icon: Play }],
  },
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
  return (
    <Quiet state={s}>
      {(c) =>
        c.unread ? (
          <span className="badge b-fail" aria-label={`${c.unread} unread alerts`}>
            {c.unread}
          </span>
        ) : null
      }
    </Quiet>
  );
}

function Identity() {
  const { user } = useAuth();
  return (
    <div className="nav-footer small">
      {user ? (
        <>
          Signed in: <strong>{user.username}</strong> · {user.role.replace(/_/g, " ")}
        </>
      ) : (
        <a href={href("governance")}>Sign in (governance)</a>
      )}
    </div>
  );
}

function PendingCount() {
  const s = useApi((sig) => getSummary({}, sig), []);
  const n = s.data?.totals.pending_reviews;
  return n ? (
    <span className="badge b-review" aria-label={`${n} pending reviews`}>
      {n}
    </span>
  ) : null;
}

export default function App() {
  const route = useRoute();
  const section = route.section === "events" && route.param ? "forensics" : route.section;
  const page = (() => {
    switch (section) {
      case "ingest":
        return <IngestPage />;
      case "events":
        return <EventExplorer />;
      case "forensics":
        return <EventForensics id={route.param!} />;
      case "sources":
        return <Sources sourceKey={route.param} />;
      case "drift":
        return <DriftQueue />;
      case "advanced-drift":
        return <AdvancedDrift />;
      case "baselines":
        return <Baselines sourceKey={route.param} />;
      case "correlations":
        return <Correlations correlationId={route.param} />;
      case "replay":
        return <ReplayPage jobId={route.param} />;
      case "onboarding":
        return <Onboarding sessionId={route.param} />;
      case "evolution":
        return <AdapterEvolution sourceKey={route.param} />;
      case "learning":
        return <Learning sessionId={route.param} />;
      case "export":
        return <ExportPage />;
      case "integrity":
        return <IntegrityPage />;
      case "alerts":
        return <AlertsPage />;
      case "audit":
        return <AuditPage />;
      case "governance":
        return <GovernancePage />;
      case "demo":
        return <Demo />;
      default:
        return <Overview />;
    }
  })();
  const activeNav = section === "forensics" ? "events" : section;
  const currentNav = NAV.flatMap((g) => g.items).find((item) => item.id === activeNav);

  return (
    <div className="shell">
      <nav className="nav" aria-label="Primary">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div>
            <div className="brand-name">
              logforge<span className="brand-ai">AI</span>
            </div>
            <div className="brand-sub">Universal Adaptive Log Intelligence</div>
          </div>
        </div>

        {NAV.map((g) => (
          <div key={g.group} style={{ display: "contents" }}>
            <div className="nav-group">{g.group}</div>
            {g.items.map((item) => {
              const Icon = item.icon;
              return (
                <a
                  key={item.id}
                  href={href(item.id)}
                  className={activeNav === item.id ? "active" : ""}
                  aria-current={activeNav === item.id ? "page" : undefined}
                >
                  <Icon size={16} aria-hidden="true" style={{ opacity: 0.85, flexShrink: 0 }} />
                  <span style={{ flex: 1 }}>{item.label}</span>
                  {item.id === "drift" && <PendingCount />}
                  {item.id === "alerts" && <UnreadAlerts />}
                </a>
              );
            })}
          </div>
        ))}

        <div className="sidebar-bottom" style={{ marginTop: "auto", paddingTop: 12 }}>
          <div className="evidence-note">
            <Fingerprint size={22} style={{ flexShrink: 0 }} />
            <div>
              <strong>Never lose the evidence.</strong>
              <div>Raw preserved · Verifiable SHA-256</div>
            </div>
          </div>
        </div>

        <Identity />
        <HealthIndicator />
      </nav>

      <main className="main" id="main">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <span className="breadcrumb-slash">/</span>
            <strong>{currentNav?.label || "Overview"}</strong>
          </div>
          <div className="topbar-right">
            <span className="version-tag">OCSF-aligned</span>
            <button
              className="icon-button"
              title="Refresh view"
              aria-label="Refresh view"
              type="button"
              onClick={() => window.dispatchEvent(new Event("hashchange"))}
            >
              <RefreshCw size={15} />
            </button>
          </div>
        </header>
        <div className="page-wrapper">{page}</div>
      </main>
    </div>
  );
}
