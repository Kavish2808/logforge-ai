import { useEffect, useState } from "react";
import { getHealth, getSummary } from "./api/endpoints";
import { useApi } from "./lib/useApi";
import { href, useRoute } from "./lib/router";
import { AdapterEvolution } from "./pages/AdapterEvolution";
import { Demo } from "./pages/Demo";
import { DriftQueue } from "./pages/DriftQueue";
import { EventExplorer } from "./pages/EventExplorer";
import { EventForensics } from "./pages/EventForensics";
import { ExportPage } from "./pages/Export";
import { Learning } from "./pages/Learning";
import { Onboarding } from "./pages/Onboarding";
import { Overview } from "./pages/Overview";
import { Sources } from "./pages/Sources";

const NAV: { group: string; items: { id: string; label: string }[] }[] = [
  { group: "Operate", items: [{ id: "overview", label: "Overview" }, { id: "events", label: "Event Explorer" }, { id: "sources", label: "Sources" }] },
  { group: "Adapt", items: [{ id: "drift", label: "Drift Queue" }, { id: "onboarding", label: "Onboarding" }, { id: "evolution", label: "Adapter Evolution" }, { id: "learning", label: "Learning" }] },
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
      case "onboarding": return <Onboarding sessionId={route.param} />;
      case "evolution": return <AdapterEvolution sourceKey={route.param} />;
      case "learning": return <Learning sessionId={route.param} />;
      case "export": return <ExportPage />;
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
              </a>
            ))}
          </div>
        ))}
        <HealthIndicator />
      </nav>
      <main className="main" id="main">{page}</main>
    </div>
  );
}
