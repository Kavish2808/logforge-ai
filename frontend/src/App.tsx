import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  Bell,
  Cable,
  Database,
  FileText,
  Fingerprint,
  GitBranch,
  GitCompareArrows,
  Hexagon,
  Layers3,
  LayoutDashboard,
  LogIn,
  LogOut,
  LucideIcon,
  Menu,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Upload,
  UserRound,
  Workflow,
  Wand2,
} from "lucide-react";
import { getAlertCounts, getHealth, getSummary, logout } from "./api/endpoints";
import { Quiet } from "./components/trust";
import { clearAuth, useAuth } from "./lib/auth";
import { useApi } from "./lib/useApi";
import { href, navigate, useRoute } from "./lib/router";
import { requestSearch, ULID } from "./lib/search";
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
import { IntegrationsPage } from "./pages/Integrations";
import { IntegrityPage } from "./pages/Integrity";
import { Learning } from "./pages/Learning";
import { LoginPage } from "./pages/Login";
import { Onboarding } from "./pages/Onboarding";
import { Overview } from "./pages/Overview";
import { Sources } from "./pages/Sources";

interface NavItem {
  id: string;
  label: string;
  icon: LucideIcon;
  description: string;
}

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Overview",
    items: [
      { id: "overview", label: "Dashboard", icon: LayoutDashboard, description: "Operational intelligence across ingestion, normalization, integrity and adaptation" },
      { id: "events", label: "Event Explorer", icon: Layers3, description: "Search and investigate normalized evidence with full lineage" },
      { id: "ingest", label: "Ingest Logs", icon: Upload, description: "Submit raw logs through the real pipeline" },
      { id: "sources", label: "Sources", icon: Database, description: "Every observed source, its adapter, baseline and drift" },
    ],
  },
  {
    group: "Adapt",
    items: [
      { id: "onboarding", label: "Adaptive Onboarding", icon: Wand2, description: "Teach LogForge a new log source without hand-written parsers" },
      { id: "evolution", label: "Adapter Evolution", icon: GitBranch, description: "Versioned adapters that evolve under evidence and approval" },
      { id: "learning", label: "Learning", icon: Sparkles, description: "Evidence-driven adapter changes, validated before approval" },
      { id: "advanced-drift", label: "Advanced Drift", icon: Workflow, description: "Structural, statistical and semantic change detection" },
      { id: "drift", label: "Drift Queue", icon: Activity, description: "Structural drift awaiting a human decision" },
      { id: "baselines", label: "Baselines", icon: ShieldCheck, description: "Current and golden baselines with poisoning evidence" },
    ],
  },
  {
    group: "Investigate",
    items: [
      { id: "correlations", label: "Correlations", icon: Radio, description: "Cross-source drift grouped into scored investigations" },
      { id: "replay", label: "Replay & Revisions", icon: RotateCcw, description: "Rate-limited replay with append-only, verifiable revisions" },
    ],
  },
  {
    group: "Trust",
    items: [
      { id: "integrity", label: "Integrity", icon: Fingerprint, description: "SHA-256, raw vault and Merkle evidence chain" },
      { id: "alerts", label: "Alerts", icon: Bell, description: "Security-relevant conditions raised by the platform" },
      { id: "audit", label: "Audit Log", icon: FileText, description: "Hash-chained record of every governed action" },
      { id: "governance", label: "Governance", icon: ShieldCheck, description: "Identity, roles, maker-checker, SLAs and configuration" },
    ],
  },
  {
    group: "Integrate",
    items: [
      { id: "export", label: "Export", icon: ArrowDownToLine, description: "Verified evidence export for SIEM and archive" },
      { id: "integrations", label: "Integrations", icon: Cable, description: "Registered destinations and alert delivery channels" },
    ],
  },
  {
    group: "Showcase",
    items: [{ id: "demo", label: "Demo", icon: Play, description: "The complete adaptive lifecycle, end to end" }],
  },
];

const FIRST_VISIT_KEY = "logforge.entered";

function HealthPill() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, []);
  const health = useApi((s) => getHealth(s), [tick]);
  const checking = health.loading && !health.data;
  const ok = health.data?.status === "ok" && !health.error;
  return (
    <span className={`pill ${checking ? "" : ok ? "ok" : "fail"}`} aria-live="polite"
      title={checking ? "Checking API…" : ok ? "API + database healthy" : "API unreachable"}>
      <span className={`dot ${checking ? "" : ok ? "ok" : "fail"}`} aria-hidden="true" style={{ marginRight: 0 }} />
      <span className="pill-text">{checking ? "Checking…" : ok ? "System healthy" : "API unreachable"}</span>
    </span>
  );
}

function AlertBell() {
  const s = useApi((sig) => getAlertCounts(sig), []);
  const unread = s.data?.unread ?? 0;
  return (
    <a className="icon-button" href={href("alerts")} title="Alerts" aria-label={unread ? `Alerts: ${unread} unread` : "Alerts"}>
      <Bell size={16} aria-hidden="true" />
      {unread > 0 && <span className="count" aria-hidden="true">{unread > 99 ? "99+" : unread}</span>}
    </a>
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

function PendingCount() {
  const s = useApi((sig) => getSummary({}, sig), []);
  const n = s.data?.totals.pending_reviews;
  return n ? (
    <span className="badge b-review" aria-label={`${n} pending reviews`}>
      {n}
    </span>
  ) : null;
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = q.trim();
    if (!text) return;
    if (ULID.test(text)) navigate("events", text.toUpperCase());
    else {
      requestSearch(text);
      navigate("events");
    }
    setQ("");
  };
  return (
    <form className="search" role="search" onSubmit={submit}>
      <Search size={14} aria-hidden="true" />
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search events, event id, SHA-256…" aria-label="Search events" />
    </form>
  );
}

function UserMenu() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!user) {
    return (
      <a className="button primary small" href={href("login")}>
        <LogIn size={14} aria-hidden="true" /> Sign in
      </a>
    );
  }
  const initials = user.username.slice(0, 2).toUpperCase();
  return (
    <div className="rel" ref={ref}>
      <button type="button" className="user-chip" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}
        aria-label={`Account menu for ${user.username}`}>
        <span className="avatar" aria-hidden="true">{initials}</span>
        <span className="who"><strong>{user.username}</strong><span>{user.role.replace(/_/g, " ")}</span></span>
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-head"><strong>{user.username}</strong>{user.role.replace(/_/g, " ")} · {user.capabilities.length} capabilities</div>
          <a role="menuitem" href={href("governance")} onClick={() => setOpen(false)}><UserRound size={15} /> Identity &amp; roles</a>
          <a role="menuitem" href={href("audit")} onClick={() => setOpen(false)}><FileText size={15} /> Audit log</a>
          <button role="menuitem" type="button" onClick={async () => {
            try { await logout(); } catch { /* token already invalid */ }
            clearAuth();
            setOpen(false);
            navigate("login");
          }}><LogOut size={15} /> Sign out</button>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const route = useRoute();
  const { user } = useAuth();
  const [navOpen, setNavOpen] = useState(false);

  // First visit with no route and no session: land on the sign-in page (anonymous use stays possible in permissive mode).
  useEffect(() => {
    if (route.section === "overview" && !window.location.hash && !user) {
      let entered = false;
      try { entered = sessionStorage.getItem(FIRST_VISIT_KEY) === "1"; } catch { entered = true; }
      if (!entered) navigate("login");
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setNavOpen(false), [route.section, route.param]);

  if (route.section === "login") {
    return <LoginPage onEnter={() => {
      try { sessionStorage.setItem(FIRST_VISIT_KEY, "1"); } catch { /* ignore */ }
      navigate("overview");
    }} />;
  }

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
      case "integrations":
        return <IntegrationsPage />;
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
  const currentGroup = NAV.find((g) => g.items.some((i) => i.id === activeNav)) ?? NAV[0];
  const currentNav = currentGroup.items.find((item) => item.id === activeNav) ?? NAV[0].items[0];

  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <nav className="nav" aria-label="Primary" id="primary-nav">
        <a className="brand" href={href("overview")} aria-label="LogForge AI dashboard">
          <div className="brand-mark" aria-hidden="true"><Hexagon size={18} strokeWidth={2.4} /></div>
          <div>
            <div className="brand-name">LOGFORGE<span className="brand-ai">AI</span></div>
            <div className="brand-sub">Universal Adaptive Log Pre-processing</div>
          </div>
        </a>

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
                  title={item.description}
                >
                  <Icon size={16} aria-hidden="true" />
                  <span className="nav-label">{item.label}</span>
                  {item.id === "drift" && <PendingCount />}
                  {item.id === "alerts" && <UnreadAlerts />}
                </a>
              );
            })}
          </div>
        ))}

        <div className="sidebar-bottom">
          <div className="evidence-note">
            <GitCompareArrows size={20} />
            <div>
              <strong>Never lose the evidence.</strong>
              <div>Raw preserved · verifiable SHA-256 · Merkle-sealed</div>
            </div>
          </div>
        </div>
      </nav>
      <div className="nav-backdrop" onClick={() => setNavOpen(false)} aria-hidden="true" />

      <main className="main" id="main">
        <header className="topbar">
          <button type="button" className="icon-button nav-toggle" aria-label={navOpen ? "Close navigation" : "Open navigation"}
            aria-controls="primary-nav" aria-expanded={navOpen} onClick={() => setNavOpen(!navOpen)}>
            <Menu size={17} />
          </button>
          <div className="topbar-title">
            <span className="crumb">{currentGroup.group}</span>
            <span className="t">{section === "forensics" ? "Event forensics" : currentNav.label}</span>
            <span className="d">{currentNav.description}</span>
          </div>
          <div className="topbar-right">
            <GlobalSearch />
            <HealthPill />
            <button className="icon-button" title="Refresh view" aria-label="Refresh view" type="button"
              onClick={() => window.dispatchEvent(new Event("hashchange"))}>
              <RefreshCw size={15} />
            </button>
            <AlertBell />
            <UserMenu />
          </div>
        </header>
        <div className="page-wrapper">{page}</div>
      </main>
    </div>
  );
}
