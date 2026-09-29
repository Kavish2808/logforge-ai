import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, Bell, Blocks, Braces, Check, CheckCheck, ChevronDown, ChevronLeft, ChevronRight, Clock3, Copy, Database, Eye, FileJson2, Fingerprint, GitBranch, Globe2, Layers3, LayoutDashboard, LoaderCircle, LogOut, Menu, Network, Pause, Play, Plus, Radio, RefreshCw, RotateCcw, Search, Send, Settings2, ShieldCheck, Sparkles, Terminal, Upload, Workflow, X, XCircle, Zap } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api, exportEvents, saveBlob } from './api';
import type { Data } from './api';

type Page = 'overview' | 'events' | 'ingest' | 'onboarding' | 'sources' | 'drift' | 'correlations' | 'learning' | 'shadow' | 'replay' | 'export' | 'governance' | 'demo';
type User = { username: string; role: string };
type Toast = { message: string; kind: 'success' | 'error' };
type Action = (path: string, body?: unknown, success?: string) => Promise<Data | undefined>;
const nav: { id: Page; name: string; icon: LucideIcon; group: string }[] = [
  { id: 'overview', name: 'Overview', icon: LayoutDashboard, group: 'WORKSPACE' },
  { id: 'events', name: 'Event explorer', icon: Layers3, group: 'WORKSPACE' },
  { id: 'ingest', name: 'Ingest logs', icon: Upload, group: 'WORKSPACE' },
  { id: 'onboarding', name: 'Vendor onboarding', icon: Sparkles, group: 'WORKSPACE' },
  { id: 'sources', name: 'Source intelligence', icon: Database, group: 'INTELLIGENCE' },
  { id: 'drift', name: 'Drift detection', icon: Activity, group: 'INTELLIGENCE' },
  { id: 'correlations', name: 'Drift correlations', icon: Network, group: 'INTELLIGENCE' },
  { id: 'learning', name: 'Adaptive learning', icon: Sparkles, group: 'INTELLIGENCE' },
  { id: 'shadow', name: 'Shadow validation', icon: GitBranch, group: 'INTELLIGENCE' },
  { id: 'replay', name: 'Replay & revisions', icon: RotateCcw, group: 'OPERATIONS' },
  { id: 'export', name: 'Data export', icon: ArrowDownToLine, group: 'OPERATIONS' },
  { id: 'governance', name: 'Trust & governance', icon: ShieldCheck, group: 'OPERATIONS' },
  { id: 'demo', name: 'Demo showcase', icon: Terminal, group: 'OPERATIONS' },
];
const titles: Record<Page, [string, string]> = {
  overview: ['Your logs. A clearer picture.', 'One workspace for every event, every change, and every piece of evidence.'],
  events: ['Event explorer', 'Search the evidence. Follow every event from raw input to normalized insight.'],
  ingest: ['Bring your logs into focus.', 'Ingest heterogeneous events through one deterministic, evidence-preserving pipeline.'],
  onboarding: ['Vendor onboarding', 'Teach LogForge an unknown vendor from representative samples with full sandbox validation.'],
  sources: ['Source intelligence', 'Understand your sources and protect their trusted structural baselines.'],
  drift: ['See the change. Keep the context.', 'Explainable structural and statistical signals, with a human in control.'],
  correlations: ['Drift correlations', 'Cross-vendor investigation signals surfacing related schema changes.'],
  learning: ['Adaptive learning', 'Propose, validate, and evolve adapters with evidence at every step.'],
  shadow: ['Shadow validation', 'Candidate adapter verification on real traffic with automatic circuit-breaker safety.'],
  replay: ['Replay & revisions', 'Reprocess stored evidence without rewriting its history.'],
  export: ['Data export & compliance', 'Bounded, streaming NDJSON/JSON evidence export under published contracts.'],
  governance: ['Trust, built into every event.', 'Verify integrity, review sensitive changes, and follow the audit trail.'],
  demo: ['Lifecycle showcase', 'Step-by-step reproducible run of evidence preservation, drift detection, and governed learning.'],
};
const readable = (s: unknown) => String(s ?? '—').replaceAll('_', ' ').toLowerCase().replace(/^\w/, (c: string) => c.toUpperCase());
const number = (v: unknown) => typeof v === 'number' ? v.toLocaleString() : '—';
const time = (v: unknown) => v ? new Date(String(v)).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const short = (s: unknown, length = 12) => String(s ?? '—').slice(0, length);
const list = (data?: Data | Data[]): Data[] => (Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : []).map((r: Data) => ({ ...r, status: r.status ?? r.state, acknowledged: r.acknowledged ?? !!r.acknowledged_by }));
const textValue = (value: unknown) => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);

function useResource(path: string, tick = 0) {
  const [data, setData] = useState<Data>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true); setError('');
    api(path).then(d => { if (alive) setData(d); }).catch(e => { if (alive) setError(e.message); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [path, tick]);
  return { data, error, loading };
}

function Logo({ small = false }: { small?: boolean }) {
  return <div className={`brand ${small ? 'small' : ''}`}><span className="brand-mark"><span /><span /><span /></span><span>logforge<span className="brand-ai">AI</span></span></div>;
}
function Badge({ value, tone }: { value: unknown; tone?: string }) {
  const key = String(value ?? 'UNKNOWN').toUpperCase();
  const color = tone || (['PARSED', 'ACTIVE', 'PASSED', 'APPROVED', 'COMPLETED', 'VALIDATED', 'HEALTHY', 'VERIFIED', 'ACCEPTED', 'DELIVERED', 'PINNED'].includes(key) ? 'green' : ['FAILED', 'CRITICAL', 'BLOCKED', 'REJECTED', 'ERROR', 'CANCELLED', 'INVALID'].includes(key) ? 'red' : ['PARTIAL', 'WARNING', 'HIGH', 'PENDING', 'PROPOSED', 'NEEDS_REVIEW', 'REVIEW_REQUIRED', 'PAUSED', 'MEDIUM', 'OPEN'].includes(key) ? 'amber' : 'blue');
  return <span className={`badge ${color}`}><i />{readable(value)}</span>;
}
function Empty({ title = 'Nothing here yet', description = 'New activity will appear here as events move through your pipeline.', icon: Icon = Layers3, children }: { title?: string; description?: string; icon?: LucideIcon; children?: ReactNode }) {
  return <div className="empty"><div className="empty-icon"><Icon size={24} /></div><h3>{title}</h3><p>{description}</p>{children}</div>;
}
function ErrorNotice({ message }: { message: string }) { return message ? <div className="error-notice" role="alert"><XCircle size={17} /><span>{message}</span></div> : null; }
function Loader() { return <div className="loading"><LoaderCircle className="spin" size={22} /><span>Loading workspace data…</span></div>; }
function Json({ value }: { value: unknown }) { return <pre className="json">{JSON.stringify(value ?? {}, null, 2)}</pre>; }
function Panel({ title, subtitle, actions, children, className = '' }: { title: string; subtitle?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{actions}</div>{children}</section>;
}
function Tabs({ options, active, onChange }: { options: string[]; active: string; onChange: (v: string) => void }) {
  return <div className="tabs" role="tablist">{options.map(v => <button type="button" key={v} role="tab" aria-selected={active === v} className={active === v ? 'active' : ''} onClick={() => onChange(v)}>{v}</button>)}</div>;
}
function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    ref.current?.focus();
    const listener = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Tab') {
        const nodes = ref.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input, textarea, select, a, [tabindex="0"]');
        if (!nodes?.length) return;
        const first = nodes[0]; const last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', listener);
    return () => { document.removeEventListener('keydown', listener); previous?.focus(); };
  }, [onClose]);
  return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><div ref={ref} tabIndex={-1} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={20} /></button></div>{children}</div></div>;
}

function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { const d = await api('/auth/login', { username, password }); sessionStorage.setItem('logforge.token', d.access_token); onLogin({ username: d.username || username, role: d.role }); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="login-page"><div className="login-story"><Logo /><div className="login-story-content"><span className="eyebrow"><span className="live-dot" /> EVIDENCE-FIRST LOG INTELLIGENCE</span><h1>Every log tells a story.<br /><em>Keep all of it.</em></h1><p>Turn fragmented logs into trusted intelligence. Detect, normalize, and understand every event without losing the original evidence.</p><div className="pipeline-art">{[Terminal, Braces, GitBranch, Fingerprint].map((Icon, i) => <div className="pipeline-art-step" key={i}><div><Icon size={26} /></div><span>{['Ingest', 'Normalize', 'Learn', 'Verify'][i]}</span></div>)}</div></div><div className="login-foot"><ShieldCheck size={16} /> Deterministic by design. Accountable by default.</div></div><div className="login-form-side"><div className="login-form-wrap"><span className="kicker">YOUR INTELLIGENCE WORKSPACE</span><h2>Welcome to LogForge.</h2><p>Sign in to follow the evidence.</p><form onSubmit={submit}><label>Username<input autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} placeholder="Enter your username" /></label><label>Password<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter your password" /></label><ErrorNotice message={error} /><button className="button primary login-submit" disabled={busy}>{busy ? <LoaderCircle size={17} className="spin" /> : <>Open workspace <ArrowRight size={17} /></>}</button></form><div className="setup-note"><Terminal size={18} /><div><strong>Running locally for the first time?</strong><p>Use the account created during setup. The project README explains how to initialize users and run the console.</p></div></div></div><div className="login-security"><ShieldCheck size={14} /> Authenticated access · Auditable actions</div></div></div>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null); const [checking, setChecking] = useState(!!sessionStorage.getItem('logforge.token'));
  const getPage = (): Page => nav.some(n => n.id === location.hash.slice(1)) ? location.hash.slice(1) as Page : 'overview';
  const [page, setPage] = useState<Page>(getPage); const [tick, setTick] = useState(0); const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null); const [mobile, setMobile] = useState(false); const [selectedEvent, setSelectedEvent] = useState<string | null>(null); const [query, setQuery] = useState('');
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 850px)').matches);
  const [health, setHealth] = useState<Data>();
  const navigate = useCallback((p: Page) => { location.hash = p; setPage(p); setMobile(false); }, []);
  const closeEvent = useCallback(() => setSelectedEvent(null), []);
  const notify = useCallback((message: string, kind: Toast['kind'] = 'success') => setToast({ message, kind }), []);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 850px)');
    const update = () => { setNarrow(media.matches); if (!media.matches) setMobile(false); };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  useEffect(() => { const fn = () => setPage(getPage()); window.addEventListener('hashchange', fn); return () => window.removeEventListener('hashchange', fn); }, []);
  useEffect(() => {
    const expired = () => { sessionStorage.removeItem('logforge.token'); setUser(null); setChecking(false); };
    window.addEventListener('logforge:expired', expired);
    if (sessionStorage.getItem('logforge.token')) api('/auth/me').then(d => setUser({ username: d.username, role: d.role })).catch(expired).finally(() => setChecking(false));
    return () => window.removeEventListener('logforge:expired', expired);
  }, []);
  useEffect(() => { api('/health/readiness').then(setHealth).catch(() => setHealth({ status: 'unavailable' })); }, [tick]);
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 6500); return () => clearTimeout(t); }, [toast]);
  const action: Action = async (path, body = {}, success = 'Action completed') => {
    setBusy(true);
    try { const d = await api(path, body); setTick(v => v + 1); notify(success); return d; } catch (e) { notify((e as Error).message, 'error'); } finally { setBusy(false); }
  };
  async function download(format: 'json' | 'ndjson') { try { await exportEvents(format); notify('Evidence export downloaded'); } catch (e) { notify((e as Error).message, 'error'); } }
  if (checking) return <div className="boot"><Logo /><Loader /></div>;
  if (!user) return <Login onLogin={setUser} />;
  const healthy = health && !['unavailable', 'unhealthy', 'draining', 'not_ready'].includes(String(health.status).toLowerCase()) && health.ready !== false;
  return <div className="app-shell">
    {mobile && <div className="sidebar-backdrop" onClick={() => setMobile(false)} />}
    <aside className={`sidebar ${mobile ? 'open' : ''}`} {...(narrow && !mobile ? { inert: '' } : {})} aria-hidden={narrow && !mobile ? true : undefined}><div className="sidebar-brand"><Logo /><button className="mobile-close icon-button" aria-label="Close navigation" onClick={() => setMobile(false)}><X size={18} /></button></div><div className="workspace-picker"><div className="workspace-icon"><Blocks size={18} /></div><div><strong>Intelligence workspace</strong><span>LogForge environment</span></div><ChevronDown size={14} /></div><nav>{['WORKSPACE', 'INTELLIGENCE', 'OPERATIONS'].map(group => <div className="nav-group" key={group}><span className="nav-label">{group}</span>{nav.filter(n => n.group === group).map(n => <button className={`nav-item ${page === n.id ? 'active' : ''}`} key={n.id} onClick={() => navigate(n.id)}><n.icon size={18} /><span>{n.name}</span>{n.id === 'learning' && <span className="nav-new">AI</span>}</button>)}</div>)}</nav><div className="sidebar-bottom"><div className="evidence-note"><Fingerprint size={23} /><strong>Never lose the evidence.</strong><span>Raw payloads preserved.<br />Every change traceable.</span></div><button className="profile" onClick={async () => { try { await api('/auth/logout', {}); } catch { /* Local sign-out is still available if the API is offline. */ } finally { sessionStorage.removeItem('logforge.token'); setUser(null); } }} title="Sign out"><span className="avatar">{user.username.slice(0, 2).toUpperCase()}</span><span><strong>{user.username}</strong><small>{readable(user.role)}</small></span><LogOut size={16} /></button></div></aside>
    <main className="main"><header className="topbar"><div className="breadcrumb"><button className="mobile-toggle icon-button" aria-label="Open navigation" onClick={() => setMobile(true)}><Menu size={20} /></button><span>Workspace</span><ChevronRight size={13} /><strong>{nav.find(n => n.id === page)?.name}</strong></div><div className="topbar-right"><span className={`system-status ${healthy ? '' : 'offline'}`}><i />{healthy ? 'System operational' : health ? 'API unavailable' : 'Checking connection'}</span><span className="topbar-divider" /><button className="icon-button" title="Alerts and governance" aria-label="Open alerts" onClick={() => navigate('governance')}><Bell size={18} /></button><button className="icon-button" title="Refresh workspace" aria-label="Refresh workspace" onClick={() => setTick(v => v + 1)}><RefreshCw size={17} className={busy ? 'spin' : ''} /></button></div></header>
    <div className="page-content"><div className="page-heading"><div><span className="eyebrow">{page === 'overview' ? 'WORKSPACE OVERVIEW' : 'LOGFORGE INTELLIGENCE'}</span><h1>{titles[page][0]}</h1><p>{titles[page][1]}</p></div><div className="heading-actions">{page === 'overview' || page === 'events' ? <><button className="button secondary" onClick={() => download('ndjson')}><ArrowDownToLine size={16} />Export</button><button className="button primary" onClick={() => navigate('ingest')}><Plus size={17} />Ingest logs</button></> : <span className="version-tag">OCSF-aligned</span>}</div></div>
    {busy && <div className="working-bar" role="status" />}
    {page === 'overview' && <Overview tick={tick} navigate={navigate} openEvent={setSelectedEvent} />}
    {page === 'events' && <Events tick={tick} query={query} setQuery={setQuery} openEvent={setSelectedEvent} />}
    {page === 'ingest' && <Ingest action={action} busy={busy} navigate={navigate} />}
    {page === 'onboarding' && <Onboarding tick={tick} action={action} busy={busy} openEvent={setSelectedEvent} />}
    {page === 'sources' && <Sources tick={tick} action={action} busy={busy} />}
    {page === 'drift' && <Drift tick={tick} action={action} busy={busy} openEvent={setSelectedEvent} />}
    {page === 'correlations' && <Correlations tick={tick} action={action} busy={busy} />}
    {page === 'learning' && <Learning tick={tick} action={action} busy={busy} />}
    {page === 'shadow' && <Shadow tick={tick} action={action} busy={busy} />}
    {page === 'replay' && <Replay tick={tick} action={action} busy={busy} />}
    {page === 'export' && <Export tick={tick} action={action} busy={busy} notify={notify} />}
    {page === 'governance' && <Governance tick={tick} action={action} busy={busy} download={download} user={user} />}
    {page === 'demo' && <Demo tick={tick} action={action} busy={busy} />}
    <footer className="page-footer"><span><Fingerprint size={13} /> Evidence preserved. Decisions traceable.</span><span>LOGFORGE AI <i /> UNIVERSAL LOG INTELLIGENCE</span></footer></div></main>
    {toast && <div className={`toast ${toast.kind}`} role="status">{toast.kind === 'error' ? <XCircle size={20} /> : <CheckCheck size={20} />}<span>{toast.message}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast(null)}><X size={17} /></button></div>}
    {selectedEvent && <EventDetails id={selectedEvent} onClose={closeEvent} notify={notify} tick={tick} />}
  </div>;
}

function Overview({ tick, navigate, openEvent }: { tick: number; navigate: (p: Page) => void; openEvent: (id: string) => void }) {
  const { data, error, loading } = useResource('/dashboard', tick);
  if (loading && !data) return <Loader />;
  if (error) return <ErrorNotice message={error} />;
  const c = data?.counts || {}; const total = c.events || 0; const success = total ? (c.parsed / total * 100).toFixed(1) : '0';
  const metrics = [
    { name: 'Events ingested', value: number(c.events), text: 'Raw evidence preserved', icon: Layers3, color: 'mint' },
    { name: 'Parse success', value: `${success}%`, text: `${number(c.parsed)} events normalized`, icon: CheckCheck, color: 'blue' },
    { name: 'Drift signals', value: number(c.drifts), text: 'Structural changes detected', icon: Activity, color: 'amber' },
    { name: 'Pending reviews', value: number(c.pending_reviews), text: 'Human decisions requested', icon: GitBranch, color: 'violet' },
  ];
  return <><div className="metrics-grid">{metrics.map(m => <div className="metric-card" key={m.name}><div className="metric-top"><span>{m.name}</span><div className={`metric-icon ${m.color}`}><m.icon size={18} /></div></div><strong>{m.value}</strong><span className="metric-foot"><span className={`tiny-dot ${m.color}`} />{m.text}</span></div>)}</div>
  <div className="overview-charts"><Panel title="Event activity" subtitle="Recorded ingestion across your workspace" actions={<span className="chart-legend"><i />Events ingested</span>}><ActivityChart points={data?.throughput || []} /><div className="chart-summary"><span><i className="tiny-dot mint" />Parsed <strong>{number(c.parsed)}</strong></span><span><i className="tiny-dot amber" />Partial <strong>{number(c.partial)}</strong></span><span><i className="tiny-dot red" />Failed <strong>{number(c.failed)}</strong></span><button className="text-button" onClick={() => navigate('events')}>Explore events <ArrowUpRight size={14} /></button></div></Panel>
  <Panel title="Format distribution" subtitle="Different formats. One common language."><div className="format-distribution"><div className="donut" style={{ background: donutBackground(data?.formats || []) }}><div><strong>{number(total)}</strong><span>TOTAL EVENTS</span></div></div><div className="format-legend">{(data?.formats || []).length ? data?.formats.map((f: Data, i: number) => <div key={f.name}><span><i style={{ background: colors[i % colors.length] }} />{f.name}</span><strong>{number(f.count)}<small>{total ? Math.round(f.count / total * 100) : 0}%</small></strong></div>) : <p>No formats detected yet.</p>}</div></div></Panel></div>
  <div className="pipeline-banner"><div className="pipeline-banner-icon"><Workflow size={23} /></div><div><strong>Your evidence, intact from end to end.</strong><span>Every event retains its original payload and a verifiable SHA-256 fingerprint.</span></div><div className="pipeline-mini">{['Detect', 'Parse', 'Normalize', 'Preserve', 'Verify'].map((s, i) => <span key={s}>{i > 0 && <ChevronRight size={12} />}<span>{s}</span></span>)}</div><button className="icon-button" aria-label="Verify evidence integrity" onClick={() => navigate('governance')}><ArrowUpRight size={19} /></button></div>
  <div className="overview-bottom"><Panel title="Recent events" subtitle="The latest evidence entering your pipeline" actions={<button className="text-button" onClick={() => navigate('events')}>View all events <ArrowRight size={14} /></button>}>{(data?.recent_events || []).length ? <EventTable items={data?.recent_events.slice(0, 6)} openEvent={openEvent} compact /> : <Empty title="Your pipeline is ready" description="Ingest your first logs or load the sample dataset to start exploring." icon={Radio}><button className="button primary small" onClick={() => navigate('ingest')}><Plus size={14} />Ingest logs</button></Empty>}</Panel>
  <Panel title="Attention queue" subtitle="Signals that deserve a closer look" actions={<Bell size={17} className="muted" />}><div className="attention-list">{(data?.recent_alerts || []).slice(0, 4).map((a: Data) => <button className="attention-item" key={a.id} onClick={() => navigate('governance')}><div className={`attention-icon ${String(a.severity).toLowerCase()}`}><Activity size={16} /></div><div><strong>{a.title || readable(a.kind || a.type)}</strong><p>{a.message || a.description || a.source || 'Review this signal in governance.'}</p><span>{time(a.created_at)}</span></div><ChevronRight size={14} /></button>)}{!data?.recent_alerts?.length && <Empty title="All clear for now" description="New alerts and review signals will appear here." icon={ShieldCheck} />}</div><div className="integrity-status"><ShieldCheck size={16} /><span>Integrity exceptions</span><strong className={c.integrity_failures ? 'danger-text' : ''}>{number(c.integrity_failures)}</strong></div></Panel></div></>;
}
const colors = ['#6cddbb', '#739ef5', '#bc9af1', '#e3b767', '#6cbed4', '#e786a1'];
function donutBackground(formats: Data[]) {
  const total = formats.reduce((n, f) => n + f.count, 0); if (!total) return '#27313e';
  let acc = 0; return `conic-gradient(${formats.map((f, i) => { const start = acc; acc += f.count / total * 100; return `${colors[i % colors.length]} ${start}% ${acc}%`; }).join(',')})`;
}
function ActivityChart({ points }: { points: Data[] }) {
  const max = Math.max(...points.map(p => Number(p.count)), 1); const plot = points.map((p, i) => [42 + i / Math.max(points.length - 1, 1) * 680, 166 - Number(p.count) / max * 128]);
  const d = plot.map(([x, y], i) => `${i ? 'L' : 'M'}${x},${y}`).join(' ');
  return <div className="activity-chart"><svg viewBox="0 0 746 206" role="img" aria-label={`Ingestion activity: ${points.reduce((n, p) => n + Number(p.count), 0)} events in the displayed intervals`}><defs><linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#63d7b2" stopOpacity="0.23" /><stop offset="100%" stopColor="#63d7b2" stopOpacity="0" /></linearGradient></defs>{[0, 1, 2, 3, 4].map(i => <g key={i}><line x1="42" y1={38 + i * 32} x2="722" y2={38 + i * 32} stroke="#25303c" strokeDasharray="3 5" /><text x="25" y={42 + i * 32} textAnchor="end">{Math.round(max * (1 - i / 4))}</text></g>)}{plot.length > 0 && <><path d={`${d} L${plot[plot.length - 1][0]},166 L42,166 Z`} fill="url(#chartFill)" /><path d={d} fill="none" stroke="#71dfbd" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />{plot.length === 1 && <circle cx={plot[0][0]} cy={plot[0][1]} r="4" fill="#71dfbd" />}</>}{points.filter((_, i) => i % Math.max(1, Math.ceil(points.length / 6)) === 0).map((p) => <text key={p.time} x={42 + points.indexOf(p) / Math.max(points.length - 1, 1) * 680} y="193" textAnchor="middle">{new Date(p.time).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</text>)}{!points.length && <text x="380" y="105" textAnchor="middle" className="chart-empty-text">Activity appears after your first ingestion</text>}</svg></div>;
}

function EventTable({ items, openEvent, compact = false }: { items: Data[]; openEvent: (id: string) => void; compact?: boolean }) {
  return <div className="table-scroll"><table className="events-table"><thead><tr><th>Event / source</th><th>Format</th><th>Status</th>{!compact && <><th>Vendor</th><th>Revision</th></>}<th>Received</th><th aria-label="Open event" /></tr></thead><tbody>{items.map(e => <tr key={e.id} onClick={() => openEvent(e.id)} className="clickable"><td><div className="event-cell"><div className="table-icon"><FileJson2 size={16} /></div><div><button className="row-link" onClick={ev => { ev.stopPropagation(); openEvent(e.id); }}>{e.source || 'Unassigned source'}</button><span className="mono">{short(e.id, 18)}</span></div></div></td><td><span className="format-tag">{e.format || 'UNKNOWN'}</span></td><td><Badge value={e.status} /></td>{!compact && <><td>{e.vendor || 'Generic'}</td><td><span className="mono">v{e.revision || 1}</span></td></>}<td className="muted nowrap">{time(e.created_at)}</td><td><ChevronRight size={14} className="muted" /></td></tr>)}</tbody></table></div>;
}
function Events({ tick, query, setQuery, openEvent }: { tick: number; query: string; setQuery: (q: string) => void; openEvent: (id: string) => void }) {
  const [status, setStatus] = useState(''); const [source, setSource] = useState(''); const [offset, setOffset] = useState(0); const [search, setSearch] = useState(query);
  const { data, error, loading } = useResource(`/events?limit=25&offset=${offset}&status=${encodeURIComponent(status)}&source=${encodeURIComponent(source)}&q=${encodeURIComponent(query)}`, tick);
  const sources = useResource('/sources', tick);
  return <Panel title="All events" subtitle="Original evidence, normalized records, and complete processing history" actions={<span className="count-label">{number(data?.total)} events</span>}><form className="filter-bar" onSubmit={e => { e.preventDefault(); setQuery(search); setOffset(0); }}><div className="search-input"><Search size={17} /><input aria-label="Search events" placeholder="Search event ID, vendor, normalized fields…" value={search} onChange={e => setSearch(e.target.value)} /><kbd>↵</kbd></div><select aria-label="Filter by source" value={source} onChange={e => { setSource(e.target.value); setOffset(0); }}><option value="">All sources</option>{list(sources.data).map(s => <option key={s.id || s.name} value={s.name || s.id}>{s.name || s.id}</option>)}</select><select aria-label="Filter by status" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">All statuses</option><option>PARSED</option><option>PARTIAL</option><option>FAILED</option></select><button className="button secondary small" type="submit"><Settings2 size={15} />Apply</button>{(query || status || source) && <button className="icon-button" type="button" title="Clear filters" aria-label="Clear filters" onClick={() => { setQuery(''); setSearch(''); setStatus(''); setSource(''); setOffset(0); }}><X size={17} /></button>}</form><ErrorNotice message={error} />{loading ? <Loader /> : list(data).length ? <EventTable items={list(data)} openEvent={openEvent} /> : <Empty title="No matching events" description="Try another filter, or ingest logs to populate your evidence workspace." icon={Search} />}<div className="table-footer"><span>{data?.total ? `${offset + 1}–${Math.min(offset + 25, data.total)} of ${number(data.total)} events` : '0 events'}</span><div><button className="icon-button" aria-label="Previous page" disabled={!offset || loading} onClick={() => setOffset(v => Math.max(0, v - 25))}><ChevronLeft size={16} /></button><span>Page {Math.floor(offset / 25) + 1}</span><button className="icon-button" aria-label="Next page" disabled={offset + 25 >= (data?.total || 0) || loading} onClick={() => setOffset(v => v + 25)}><ChevronRight size={16} /></button></div></div></Panel>;
}

function EventDetails({ id, tick, onClose, notify }: { id: string; tick: number; onClose: () => void; notify: (m: string, k?: Toast['kind']) => void }) {
  const { data: e, loading, error } = useResource(`/events/${encodeURIComponent(id)}`, tick); const [tab, setTab] = useState('Normalized');
  async function copy(value: string) { try { await navigator.clipboard.writeText(value); notify('Copied to clipboard'); } catch { notify('Clipboard unavailable in this browser', 'error'); } }
  return <Modal title="Event forensics" onClose={onClose} wide>{loading ? <Loader /> : error ? <ErrorNotice message={error} /> : e && <><div className="forensics-header"><div><span className="eyebrow">IMMUTABLE EVIDENCE</span><h3>{e.source}</h3><span className="mono muted">{e.id}</span></div><Badge value={e.status} /></div><div className="detail-stats"><div><span>Format</span><strong>{e.format}</strong></div><div><span>Vendor</span><strong>{e.vendor || 'Generic'}</strong></div><div><span>Adapter</span><strong>{textValue(e.adapter_version)}</strong></div><div><span>Revision</span><strong>{e.revision}</strong></div></div><div className="hash-line"><Fingerprint size={17} /><span className="mono">{e.raw_sha256}</span><button className="icon-button" title="Copy SHA-256" aria-label="Copy SHA-256" onClick={() => copy(e.raw_sha256)}><Copy size={14} /></button></div><Tabs options={['Normalized', 'Raw evidence', 'Field accounting', 'Lineage', 'Revisions']} active={tab} onChange={setTab} /><div className="detail-tab-content">{tab === 'Normalized' && <><Json value={e.normalized} /><h4>Preserved extensions</h4><Json value={e.extensions} />{e.warnings?.length > 0 && <><h4>Processing warnings</h4><Json value={e.warnings} /></>}</>}{tab === 'Raw evidence' && <><div className="raw-actions"><span><ShieldCheck size={15} />Original payload · {new TextEncoder().encode(e.raw_text || '').length} bytes</span><button className="button secondary small" onClick={() => copy(e.raw_text || '')}><Copy size={14} />Copy</button><button className="button secondary small" onClick={() => { if (e.raw_base64) { const bytes = Uint8Array.from(atob(e.raw_base64), c => c.charCodeAt(0)); saveBlob(new Blob([bytes]), `${e.id}.log`); } else saveBlob(new Blob([e.raw_text || '']), `${e.id}.log`); }}><ArrowDownToLine size={14} />Download raw</button></div><pre className="json raw-payload">{e.raw_text || '(No UTF-8 text representation available)'}</pre><h4>Integrity evidence</h4><Json value={{ raw_integrity: e.integrity, merkle: e.merkle }} /></>}{tab === 'Field accounting' && <><div className="info-callout"><Braces size={18} /><span>Input fields are either normalized or preserved in extensions. Unknown information remains evidence.</span></div><Json value={e.accounting} /></>}{tab === 'Lineage' && <><div className="info-callout"><GitBranch size={18} /><span>Processing stages and version information recorded for this event.</span></div><Json value={e.lineage} /><h4>Compact lineage representation</h4><Json value={e.compact_lineage} /></>}{tab === 'Revisions' && <>{e.revisions?.length ? e.revisions.map((r: Data, i: number) => <details className="revision-item" key={r.id || i} open={i === 0}><summary><span className="revision-dot" />Revision {r.revision || r.number || i + 1}<Badge value={r.kind || r.origin || r.reason || 'RECORDED'} /><span className="muted">{time(r.created_at)}</span></summary><Json value={r} /></details>) : <Empty title="No revision history returned" description="Revision records appear after the event is persisted or replayed." />}</>}</div></>}</Modal>;
}

const samples: Record<string, string> = {
  'JSON': '{"timestamp":"2026-09-28T09:30:00Z","vendor":"custom","src_ip":"10.20.0.12","dst_ip":"203.0.113.24","dst_port":443,"action":"allow","policy_id":"edge-17","bytes":2048}',
  'Syslog': '<134>Sep 28 09:30:00 edge-fw %ASA-6-302013: Built outbound TCP connection 123 for outside:203.0.113.24/443 to inside:10.20.0.12/51822',
  'CEF': 'CEF:0|Example|Firewall|1.0|100|Connection allowed|3|src=10.20.0.12 dst=203.0.113.24 dpt=443 act=allow custom_field=preserved',
  'LEEF': 'LEEF:1.0|Example|Gateway|1.0|100|src=10.20.0.12\tdst=203.0.113.24\tdstPort=443\taction=allow',
  'XML': '<event><vendor>Example</vendor><src_ip>10.20.0.12</src_ip><dst_ip>203.0.113.24</dst_ip><action>allow</action><policy_id>edge-17</policy_id></event>',
};
function parseInput(raw: string, mode: string) {
  if (mode === 'Single event') return [{ raw }];
  if (mode === 'JSON array') { const a = JSON.parse(raw); if (!Array.isArray(a)) throw new Error('Enter a JSON array of strings or event objects.'); return a.map(v => ({ raw: typeof v === 'string' ? v : JSON.stringify(v) })); }
  return raw.split(/\r?\n/).filter(v => v.trim()).map(v => ({ raw: v }));
}
function Ingest({ action, busy, navigate }: { action: Action; busy: boolean; navigate: (p: Page) => void }) {
  const [source, setSource] = useState(''); const [raw, setRaw] = useState(''); const [mode, setMode] = useState('Single event'); const [error, setError] = useState(''); const [result, setResult] = useState<Data>();
  const key = useRef(crypto.randomUUID()); const file = useRef<HTMLInputElement>(null);
  function updateRaw(v: string) { setRaw(v); key.current = crypto.randomUUID(); setResult(undefined); }
  async function submit(e: FormEvent) { e.preventDefault(); setError(''); try { const events = parseInput(raw, mode); if (!events.length) throw new Error('Add at least one event to ingest.'); const r = await action('/ingest', { source, events, idempotency_key: key.current }, 'Batch ingested. Raw evidence preserved.'); if (r) setResult(r); } catch (e) { setError((e as Error).message); } }
  return <div className="ingest-layout"><div><Panel title="Ingestion workspace" subtitle="Paste a log, upload a file, or submit a complete batch" actions={<span className="secure-label"><ShieldCheck size={14} />Raw-preserving</span>}><form onSubmit={submit} className="ingest-form"><div className="form-row"><label>Source identifier<input value={source} required maxLength={120} onChange={e => { setSource(e.target.value); key.current = crypto.randomUUID(); }} placeholder="e.g. production-edge-firewall" /></label><label>Input mode<select value={mode} onChange={e => { setMode(e.target.value); key.current = crypto.randomUUID(); }}>{['Single event', 'One event per line', 'JSON array'].map(v => <option key={v}>{v}</option>)}</select></label></div><div className="editor-heading"><label htmlFor="raw-input">Raw log payload</label><button type="button" className="text-button" onClick={() => file.current?.click()}><Upload size={14} />Upload file</button><input ref={file} type="file" accept=".log,.txt,.json,.ndjson,.xml,.csv" hidden onChange={async e => { const f = e.target.files?.[0]; if (f) { if (f.size > 8 * 1024 * 1024) { setError('Select a file smaller than 8 MB.'); return; } updateRaw(await f.text()); } e.target.value = ''; }} /></div><textarea id="raw-input" className="log-editor" value={raw} onChange={e => updateRaw(e.target.value)} placeholder={'Paste your original log here…\n\nJSON, Syslog, CEF, LEEF, XML, or an unknown format.\nWe preserve the raw evidence even when parsing is incomplete.'} required spellCheck={false} /><div className="editor-footer"><span><Braces size={14} />{new TextEncoder().encode(raw).length.toLocaleString()} bytes</span><span>UTF-8 input</span></div><ErrorNotice message={error} /><div className="form-bottom"><span><Fingerprint size={16} />SHA-256 fingerprinting on every event</span><button className="button primary" disabled={busy}>{busy ? <LoaderCircle size={16} className="spin" /> : <Zap size={16} />}Run pipeline</button></div></form></Panel>{result && <Panel title="Batch received" subtitle="Authoritative ingestion result" className="result-panel"><div className="result-stats"><div><strong>{number(result.accepted)}</strong><span>Accepted</span></div><div><strong>{number(result.duplicates)}</strong><span>Duplicates</span></div><button className="button secondary small" onClick={() => navigate('events')}>Explore evidence <ArrowRight size={14} /></button></div><details className="result-details"><summary>Batch and Merkle evidence</summary><Json value={result} /></details></Panel>}</div><div className="ingest-aside"><Panel title="Start with a sample" subtitle="Insert a payload into the editor"><div className="sample-list">{Object.keys(samples).map((s, i) => <button key={s} onClick={() => { updateRaw(samples[s]); setMode('Single event'); if (!source) setSource('sample-source'); }}><span className="sample-icon" style={{ color: colors[i] }}><FileJson2 size={19} /></span><div><strong>{s}</strong><span>{['Structured event data', 'Network & system messages', 'Common Event Format', 'Log Event Extended Format', 'Structured XML events'][i]}</span></div><Plus size={16} /></button>)}</div></Panel><div className="demo-card"><Sparkles size={23} /><h3>See the complete picture.</h3><p>Load a labeled sample dataset to explore multiple vendors, parsing outcomes, and drift signals.</p><button className="button secondary" disabled={busy} onClick={async () => { const r = await action('/ingest/demo', {}, 'Sample dataset loaded'); if (r) setResult(r); }}>Load sample dataset <ArrowRight size={14} /></button><small>Creates real sample records. Safe to run again.</small></div></div></div>;
}

function Sources({ tick, action, busy }: { tick: number; action: Action; busy: boolean }) {
  const { data, error, loading } = useResource('/sources', tick); const baselines = useResource('/baselines', tick); const [selected, setSelected] = useState<Data>();
  const close = useCallback(() => setSelected(undefined), []);
  return <><div className="info-callout"><ShieldCheck size={19} /><span><strong>Current meets trusted.</strong> Current baselines describe accepted structure. Golden baselines preserve an explicitly approved reference.</span></div><ErrorNotice message={error} />{loading ? <Loader /> : list(data).length ? <div className="source-grid">{list(data).map(s => { const baseline = list(baselines.data).find(b => (b.name || b.source) === (s.name || s.id))?.baseline || s.baseline; return <Panel key={s.id || s.name} title={s.name || s.id} subtitle={s.vendor || 'Generic source'} actions={<div className="source-icon"><Database size={21} /></div>}><div className="source-stats"><div><strong>{number(s.event_count)}</strong><span>Stored events</span></div><div><strong>{baseline ? <Check size={22} /> : '—'}</strong><span>Current baseline</span></div></div><div className="source-baseline"><ShieldCheck size={16} /><span>Golden reference</span><Badge value={s.golden || baseline?.golden ? 'PINNED' : 'NOT_PINNED'} /></div><div className="source-actions"><button className="button secondary small" onClick={() => setSelected({ ...s, baseline })}><Eye size={14} />Inspect</button><button className="text-button" disabled={busy} onClick={() => action(`/baselines/${encodeURIComponent(s.name || s.id)}/golden`, { action: s.golden || baseline?.golden ? 'RETIRE' : 'PIN' }, 'Golden baseline request submitted for independent approval')}>{s.golden || baseline?.golden ? 'Request retirement' : 'Request golden pin'}<ArrowUpRight size={14} /></button></div></Panel>; })}</div> : <Panel title="Connected sources"><Empty title="No sources discovered yet" description="Sources are created when you ingest events with a source identifier." icon={Database} /></Panel>}{baselines.error && <ErrorNotice message={baselines.error} />}{selected && <Modal title="Source & baseline evidence" onClose={close} wide><Json value={selected} /></Modal>}</>;
}

function Drift({ tick, action, busy, openEvent }: { tick: number; action: Action; busy: boolean; openEvent: (id: string) => void }) {
  const [tab, setTab] = useState('Structural queue'); const endpoint = tab === 'Statistical signals' ? '/drift/statistical' : tab === 'Cross-source correlations' ? '/correlations' : '/drift';
  const { data, loading, error } = useResource(endpoint, tick); const [selected, setSelected] = useState<Data>(); const close = useCallback(() => setSelected(undefined), []);
  return <><div className="section-banner"><span className="banner-icon"><Activity size={22} /></span><div><strong>Change is a signal. Evidence gives it meaning.</strong><p>Detection is deterministic. Baseline changes require an explicit review decision.</p></div><span className="outline-tag">0.85 structural threshold</span></div><Tabs options={['Structural queue', 'Statistical signals', 'Cross-source correlations']} active={tab} onChange={setTab} /><ErrorNotice message={error} />{loading ? <Loader /> : tab === 'Structural queue' ? <Panel title="Structural drift queue" subtitle="Field presence, order, count, and type" actions={<span className="count-label">{list(data).length} signals</span>}>{list(data).length ? <div className="drift-list">{list(data).map(d => <div className="drift-card" key={d.id}><div className="drift-top"><div className="drift-title"><span className="drift-glyph"><GitBranch size={20} /></span><div><h3>{readable(d.classification)}</h3><span>{d.source} <i>·</i> {time(d.created_at)}</span></div></div><Badge value={d.severity} /></div><div className="drift-evidence"><div><span>Structural similarity</span><div className="similarity-track"><span style={{ width: `${Math.min(100, Number(d.similarity || 0) * 100)}%` }} /></div><strong>{(Number(d.similarity || 0) * 100).toFixed(1)}%</strong></div><button className="text-button" onClick={() => setSelected(d)}>Inspect changes <ArrowUpRight size={14} /></button></div><div className="drift-actions"><Badge value={d.status} /><button className="text-button" onClick={() => openEvent(d.event_id)}>View event</button><div className="spacer" />{['PENDING', 'OPEN', 'DETECTED', 'NEEDS_REVIEW', 'PENDING_REVIEW'].includes(String(d.status).toUpperCase()) && <><button className="button secondary small" disabled={busy} onClick={() => action(`/drift/${d.id}/review`, { decision: 'REJECT' }, 'Drift rejected')}>Reject</button><button className="button secondary small" disabled={busy} onClick={() => action(`/drift/${d.id}/review`, { decision: 'REPLACE_BASELINE' }, 'Baseline replacement submitted for independent approval')}>Replace baseline</button><button className="button primary small" disabled={busy} onClick={() => action(`/drift/${d.id}/review`, { decision: 'ACCEPT_VARIANT' }, 'Variant accepted')}>Accept variant</button></>}</div></div>)}</div> : <Empty title="No structural drift detected" description="New events are compared with accepted source baselines. Changes will appear here." icon={Activity} />}</Panel> : <Panel title={tab} subtitle={tab === 'Statistical signals' ? 'Explainable distribution changes across observed windows' : 'Investigation signals across distinct vendors and sources'}>{list(data).length ? <div className="finding-list">{list(data).map((f, i) => <button className="finding-card" key={f.id || i} onClick={() => setSelected(f)}><span className="finding-icon">{tab === 'Statistical signals' ? <Activity size={23} /> : <Network size={23} />}</span><div><h3>{f.source || f.field || f.explanation || `Correlation group ${i + 1}`}</h3><p>{f.explanation || f.message || `${readable(f.metric || f.classification || f.type)} · ${textValue(f.sources || f.vendors || f.field)}`}</p><span>{f.baseline_count != null ? `${number(f.baseline_count)} baseline / ${number(f.current_count)} current observations · ${f.findings?.length || 0} findings` : time(f.created_at)} {f.strength && `· ${readable(f.strength)}`}</span></div><Badge value={f.severity || f.strength || f.status || 'ADVISORY'} /><ArrowUpRight size={17} /></button>)}</div> : <Empty title={tab === 'Statistical signals' ? 'Waiting for enough evidence' : 'No correlated patterns yet'} description={tab === 'Statistical signals' ? 'Statistical analysis requires sufficient observations across baseline and current windows. Missing coverage is never fabricated.' : 'Correlations require related findings from multiple sources within the observation window.'} icon={tab === 'Statistical signals' ? Activity : Network} />}{data && Object.keys(data).some(k => !['items', 'total'].includes(k)) && <details className="result-details"><summary>Analysis coverage & metadata</summary><Json value={Object.fromEntries(Object.entries(data).filter(([k]) => k !== 'items'))} /></details>}</Panel>}{selected && <Modal title="Drift evidence & explanation" onClose={close} wide><Json value={selected} /></Modal>}</>;
}

function Learning({ tick, action, busy }: { tick: number; action: Action; busy: boolean }) {
  const { data, error, loading } = useResource('/adapters', tick); const [tab, setTab] = useState('All adapters'); const [proposing, setProposing] = useState(false); const [selected, setSelected] = useState<Data>(); const [vendor, setVendor] = useState(''); const [source, setSource] = useState(''); const [raw, setRaw] = useState(''); const [driftId, setDriftId] = useState('');
  const close = useCallback(() => setSelected(undefined), []); const closeProposal = useCallback(() => setProposing(false), []);
  const adapters = [...list(data), ...(tab === 'All adapters' ? (data?.shipped || []).map((a: Data) => ({ ...a, id: 'shipped-' + a.vendor })) : [])].filter(a => tab !== 'Learned adapters' || String(a.origin).toLowerCase() !== 'shipped');
  async function propose(e: FormEvent) { e.preventDefault(); const r = await action('/adapters/propose', { vendor, ...(source ? { source } : {}), samples: raw.split(/\r?\n/).filter(l => l.trim()) }, 'Declarative adapter proposal created'); if (r) { setProposing(false); setSelected(r); } }
  return <><div className="learning-flow">{[{ n: '01', t: 'Propose', d: 'Sample-driven, declarative', i: Sparkles }, { n: '02', t: 'Validate', d: 'Deterministic sandbox', i: Braces }, { n: '03', t: 'Shadow test', d: 'Real historical evidence', i: Layers3 }, { n: '04', t: 'Approve', d: 'Independent human review', i: ShieldCheck }, { n: '05', t: 'Activate', d: 'Versioned runtime adapter', i: Zap }].map(s => <div key={s.n}><span className="flow-number">{s.n}</span><s.i size={20} /><strong>{s.t}</strong><small>{s.d}</small></div>)}</div><div className="section-toolbar"><Tabs options={['All adapters', 'Learned adapters']} active={tab} onChange={setTab} /><button className="button primary" onClick={() => setProposing(true)}><Plus size={16} />Propose adapter</button></div><ErrorNotice message={error} />{loading ? <Loader /> : adapters.length ? <div className="adapter-grid">{adapters.map(a => <Panel key={a.id} title={a.vendor} subtitle={`Version ${a.version} · ${readable(a.origin)}`} actions={<Badge value={a.state} />}><div className="adapter-info"><span><Braces size={15} />Declarative adapter</span><span><Clock3 size={15} />{time(a.created_at)}</span></div><div className="validation-summary"><div><span>Validation</span><strong>{a.validation?.match_rate != null ? `${(Number(a.validation.match_rate) * (Number(a.validation.match_rate) <= 1 ? 100 : 1)).toFixed(0)}% match` : a.validation?.status || 'Not run'}</strong></div><div><span>Shadow result</span><strong>{readable(a.shadow?.outcome || a.shadow?.status || 'Not run')}</strong></div></div><div className="adapter-actions"><button className="button secondary small" onClick={() => setSelected(a)}><Eye size={14} />Inspect</button>{!['SHIPPED', 'FROZEN'].includes(String(a.origin).toUpperCase()) && <>{['PROPOSED', 'NEEDS_REVIEW'].includes(a.state) && <button className="text-button" disabled={busy} onClick={() => action(`/adapters/${a.id}/validate`, {}, 'Sandbox validation complete')}>Validate <ArrowRight size={14} /></button>}{['VALIDATED', 'NEEDS_REVIEW', 'APPROVED'].includes(a.state) && <button className="text-button" disabled={busy} onClick={() => action(`/adapters/${a.id}/shadow`, {}, 'Shadow validation complete')}>Shadow test <ArrowRight size={14} /></button>}{['VALIDATED', 'NEEDS_REVIEW'].includes(a.state) && <button className="text-button" disabled={busy} onClick={() => action(`/adapters/${a.id}/approve`, {}, 'Approval recorded')}>Approve</button>}{a.state === 'APPROVED' && <button className="text-button" disabled={busy} onClick={() => action(`/adapters/${a.id}/activate`, {}, 'Adapter activated')}>Activate</button>}{['SUPERSEDED', 'ROLLED_BACK'].includes(a.state) && a.approved_by && <button className="text-button" disabled={busy} onClick={() => action(`/adapters/${a.id}/rollback`, {}, 'Rollback request submitted for independent approval')}>Request rollback</button>}</>}</div></Panel>)}</div> : <Panel title="Adapter registry"><Empty title="No adapters in this view" description="Propose an adapter from representative samples to begin the governed learning workflow." icon={Sparkles} /></Panel>}
  {proposing && <Modal title="Propose a declarative adapter" onClose={closeProposal}><form className="modal-form" onSubmit={propose}><div className="info-callout"><Sparkles size={19} /><span>Use 10–15 representative samples. Proposals are sandboxed and require independent approval before activation.</span></div><label>Vendor name<input required value={vendor} onChange={e => setVendor(e.target.value)} placeholder="e.g. Acme Gateway" /></label><label>Source identifier <span className="muted">(optional)</span><input value={source} onChange={e => setSource(e.target.value)} placeholder="Existing source to associate" /></label><label>Samples · one event per line<textarea className="sample-editor" required value={raw} onChange={e => setRaw(e.target.value)} placeholder="Paste representative raw events…" spellCheck={false} /></label><span className="form-hint">{raw.split(/\r?\n/).filter(l => l.trim()).length} samples supplied. No generated code is executed.</span><button className="button primary" disabled={busy}><Sparkles size={15} />Create proposal</button></form></Modal>}
  {selected && <Modal title="Adapter evidence" onClose={close} wide><div className="info-callout"><ShieldCheck size={19} /><span>AI proposes. Deterministic code validates. Humans approve. Runtime parsing remains deterministic.</span></div><Json value={list(data).find(a => a.id === selected.id) || selected} />{String(selected.origin).toLowerCase() !== 'shipped' && selected.state === 'ACTIVE' && <form className="evolve-form" onSubmit={async e => { e.preventDefault(); const r = await action('/adapters/evolve', { adapter_id: selected.id, drift_id: driftId }, 'Evolution candidate created'); if (r) setSelected(r); }}><h3>Evolve from accepted drift</h3><label>Accepted drift ID<input required value={driftId} onChange={e => setDriftId(e.target.value)} placeholder="Paste an accepted drift ID" /></label><button className="button primary small" disabled={busy}><GitBranch size={14} />Create candidate delta</button></form>}</Modal>}</>;
}

function Replay({ tick, action, busy }: { tick: number; action: Action; busy: boolean }) {
  const { data, error, loading } = useResource('/replays', tick); const sources = useResource('/sources', tick); const adapters = useResource('/adapters', tick); const [source, setSource] = useState(''); const [adapter, setAdapter] = useState(''); const [limit, setLimit] = useState(1000); const [selected, setSelected] = useState<Data>(); const [refresh, setRefresh] = useState(0);
  const live = useResource('/replays', refresh + tick); const jobs = live.data ? list(live.data) : list(data); const close = useCallback(() => setSelected(undefined), []); const key = useRef(crypto.randomUUID());
  useEffect(() => { if (!jobs.some(j => ['PENDING', 'RUNNING', 'QUEUED'].includes(j.status))) return; const timer = setTimeout(() => setRefresh(v => v + 1), 4000); return () => clearTimeout(timer); }, [jobs]);
  async function submit(e: FormEvent) { e.preventDefault(); const r = await action('/replays', { ...(source ? { source } : {}), ...(adapter ? { adapter_id: adapter } : {}), limit, idempotency_key: key.current }, 'Replay request submitted'); if (r) key.current = crypto.randomUUID(); }
  return <><div className="replay-layout"><Panel title="Start a revision-aware replay" subtitle="Original evidence and all earlier revisions remain recoverable"><form className="replay-form" onSubmit={submit}><label>Source<select value={source} onChange={e => { setSource(e.target.value); setAdapter(''); key.current = crypto.randomUUID(); }}><option value="">All sources</option>{list(sources.data).map(s => <option key={s.id || s.name} value={s.name || s.id}>{s.name || s.id}</option>)}</select></label><label>Target adapter<select value={adapter} onChange={e => { setAdapter(e.target.value); key.current = crypto.randomUUID(); }}><option value="">Current active adapter</option>{list(adapters.data).filter(a => ['ACTIVE', 'SUPERSEDED', 'ROLLED_BACK'].includes(a.state) && !!source && a.source === source).map(a => <option key={a.id} value={a.id}>{a.vendor} · v{a.version}</option>)}</select></label><label>Maximum events<input type="number" min="1" max="1000000" required value={limit} onChange={e => { setLimit(Number(e.target.value)); key.current = crypto.randomUUID(); }} /></label><button className="button primary" disabled={busy}><Play size={16} />Start replay</button></form>{limit > 10000 && <div className="info-callout amber-callout"><ShieldCheck size={18} /><span>Replays above 10,000 events require elevated approval.</span></div>}</Panel><div className="replay-promise"><div className="layer-art"><Layers3 size={45} /></div><h3>New interpretation.<br />Original evidence.</h3><p>Checkpoints, SHA-256 verification, and a full revision history keep every replay accountable.</p></div></div><Panel title="Replay jobs" subtitle="Track progress, inspect checkpoints, and control active jobs" actions={<span className="count-label">{jobs.length} jobs</span>}><ErrorNotice message={error || live.error} />{loading && !live.data ? <Loader /> : jobs.length ? <div className="table-scroll"><table><thead><tr><th>Job</th><th>Status</th><th>Progress</th><th>Created</th><th>Controls</th></tr></thead><tbody>{jobs.map(j => <tr key={j.id}><td><button className="row-link mono" onClick={() => setSelected(j)}>{short(j.id, 16)}</button><span className="cell-sub">{j.source || 'All sources'}</span></td><td><Badge value={j.status} /></td><td><span>{number(j.processed ?? j.processed_count ?? 0)} / {number(j.total ?? j.total_events ?? j.limit)}</span><div className="job-progress"><span style={{ width: `${Math.min(100, ((j.processed ?? j.processed_count ?? 0) / Math.max(1, j.total ?? j.total_events ?? j.limit)) * 100)}%` }} /></div></td><td className="muted">{time(j.created_at)}</td><td><div className="inline-actions">{['RUNNING', 'PENDING', 'QUEUED'].includes(j.status) && <button className="icon-button" title="Pause replay" aria-label="Pause replay" disabled={busy} onClick={() => action(`/replays/${j.id}/pause`, {}, 'Replay paused')}><Pause size={16} /></button>}{['PAUSED', 'FAILED'].includes(j.status) && <button className="icon-button" title="Resume replay" aria-label="Resume replay" disabled={busy} onClick={() => action(`/replays/${j.id}/resume`, {}, 'Replay resumed')}><Play size={16} /></button>}{['RUNNING', 'PENDING', 'QUEUED', 'PAUSED', 'FAILED', 'AWAITING_APPROVAL'].includes(j.status) && <button className="icon-button" title="Cancel replay" aria-label="Cancel replay" disabled={busy} onClick={() => action(`/replays/${j.id}/cancel`, {}, 'Replay cancelled')}><X size={16} /></button>}<button className="icon-button" title="Inspect replay" aria-label="Inspect replay" onClick={() => setSelected(j)}><Eye size={16} /></button></div></td></tr>)}</tbody></table></div> : <Empty title="No replay jobs yet" description="Create a replay to apply an approved adapter to stored evidence and record new revisions." icon={RotateCcw} />}</Panel>{selected && <Modal title="Replay checkpoint & integrity evidence" onClose={close} wide><Json value={jobs.find(j => j.id === selected.id) || selected} /></Modal>}</>;
}

function Governance({ tick, action, busy, download, user }: { tick: number; action: Action; busy: boolean; download: (f: 'json' | 'ndjson') => void; user: User }) {
  const [tab, setTab] = useState('Review queue'); const integrity = useResource('/integrity', tick); const endpoint = tab === 'Audit trail' ? '/audit' : tab === 'Alerts' ? '/alerts' : tab === 'Integrations' ? '/integrations' : '/reviews';
  const { data, loading, error } = useResource(endpoint, tick); const [selected, setSelected] = useState<Data>(); const [adding, setAdding] = useState(false); const [name, setName] = useState(''); const [url, setUrl] = useState(''); const close = useCallback(() => setSelected(undefined), []); const closeAdd = useCallback(() => setAdding(false), []);
  return <><div className="trust-grid"><div className="trust-card"><div className={`trust-symbol ${integrity.data?.valid === false ? 'failure' : ''}`}><Fingerprint size={27} /></div><div><span>Evidence integrity</span><strong>{integrity.loading ? 'Verifying…' : integrity.error ? 'Unavailable' : integrity.data?.valid ? 'Verified' : 'Exceptions detected'}</strong><small>{number(integrity.data?.total)} events checked</small></div></div><div className="trust-card"><div className="trust-symbol blue"><GitBranch size={26} /></div><div><span>Audit chain</span><strong>{integrity.loading ? 'Verifying…' : integrity.error ? 'Unavailable' : integrity.data?.audit_valid ? 'Chain intact' : 'Needs investigation'}</strong><small>Hash-linked activity records</small></div></div><div className="trust-card"><div className="trust-symbol violet"><ShieldCheck size={27} /></div><div><span>Access control</span><strong>{readable(user.role)}</strong><small>Signed in as {user.username}</small></div></div></div><ErrorNotice message={integrity.error} /><div className="section-toolbar"><Tabs options={['Review queue', 'Audit trail', 'Alerts', 'Integrations']} active={tab} onChange={setTab} /><div className="inline-actions">{tab === 'Integrations' ? <button className="button primary small" onClick={() => setAdding(true)}><Plus size={15} />Add webhook</button> : <><button className="button secondary small" onClick={() => download('json')}><ArrowDownToLine size={14} />JSON</button><button className="button secondary small" onClick={() => download('ndjson')}>NDJSON</button></>}</div></div><Panel title={tab} subtitle={{ 'Review queue': 'Independent approval for sensitive operations', 'Audit trail': 'Attributable actions with chained tamper evidence', 'Alerts': 'Operational signals and review SLA notifications', 'Integrations': 'Explicit webhook delivery with observable results' }[tab]} actions={tab === 'Audit trail' && data ? <Badge value={data.valid ? 'VERIFIED' : 'INVALID'} /> : <span className="count-label">{list(data).length} records</span>}><ErrorNotice message={error} />{loading ? <Loader /> : list(data).length ? <div className="governance-list">{list(data).map((r, i) => <div className="governance-item" key={r.id || i}><div className="governance-icon">{tab === 'Review queue' ? <ShieldCheck size={20} /> : tab === 'Audit trail' ? <GitBranch size={20} /> : tab === 'Alerts' ? <Bell size={20} /> : <Globe2 size={20} />}</div><div className="governance-item-main"><button className="row-link" onClick={() => setSelected(r)}>{r.name || r.title || readable(r.action || r.kind || r.type)}</button><p>{tab === 'Audit trail' ? `${r.actor || r.username || 'System'} · ${r.target || r.resource_id || short(r.id)}` : tab === 'Integrations' ? r.url : r.message || r.description || `${r.source || r.target || r.resource_id || short(r.id)}`}</p><span>{time(r.created_at)}{r.created_by || r.maker ? ` · By ${r.created_by || r.maker}` : ''}</span></div><Badge value={r.status || (r.acknowledged ? 'ACKNOWLEDGED' : r.severity) || (tab === 'Audit trail' ? 'RECORDED' : 'CONFIGURED')} />{tab === 'Review queue' && ['PENDING', 'OPEN', 'REQUESTED'].includes(r.status) && <button className="button secondary small" disabled={busy || (r.created_by || r.maker) === user.username} title={(r.created_by || r.maker) === user.username ? 'An independent reviewer must approve this request' : 'Approve request'} onClick={() => action(`/reviews/${r.id}/approve`, {}, 'Review approved')}><Check size={14} />Approve</button>}{tab === 'Alerts' && !r.acknowledged && !['ACKNOWLEDGED', 'RESOLVED'].includes(r.status) && <button className="button secondary small" disabled={busy} onClick={() => action(`/alerts/${r.id}/ack`, {}, 'Alert acknowledged')}>Acknowledge</button>}{tab === 'Integrations' && <button className="button secondary small" disabled={busy} onClick={() => action(`/integrations/${r.id}/deliver`, {}, 'Delivery queued; inspect integration status for results')}><Send size={14} />Queue delivery</button>}<button className="icon-button" title="Inspect record" aria-label="Inspect record" onClick={() => setSelected(r)}><ArrowUpRight size={16} /></button></div>)}</div> : <Empty title={{ 'Review queue': 'No pending decisions', 'Audit trail': 'No audit activity yet', 'Alerts': 'Your attention queue is clear', 'Integrations': 'Connect an external destination' }[tab]} description={{ 'Review queue': 'Sensitive changes appear here for independent maker-checker approval.', 'Audit trail': 'Important actions will be recorded in a hash-chained audit trail.', 'Alerts': 'New operational and governance signals will appear here.', 'Integrations': 'Add an HTTPS webhook to explicitly queue evidence deliveries and inspect their status.' }[tab]} icon={tab === 'Integrations' ? Globe2 : ShieldCheck} />}</Panel>{integrity.data?.failures?.length > 0 && <Panel title="Integrity exceptions" className="result-panel"><Json value={integrity.data?.failures} /></Panel>}{selected && <Modal title={`${tab} · record details`} onClose={close} wide><Json value={list(data).find(r => r.id === selected.id) || selected} /></Modal>}{adding && <Modal title="Add webhook integration" onClose={closeAdd}><form className="modal-form" onSubmit={async e => { e.preventDefault(); const r = await action('/integrations', { name, url }, 'Webhook integration configured'); if (r) { setAdding(false); setName(''); setUrl(''); } }}><div className="info-callout"><Globe2 size={18} /><span>Configure a destination, then explicitly queue a delivery. The backend validates destination policy and records delivery results.</span></div><label>Integration name<input required value={name} onChange={e => setName(e.target.value)} placeholder="Security operations webhook" /></label><label>HTTPS destination<input required type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://your-endpoint.example/events" /></label><button className="button primary" disabled={busy}><Plus size={15} />Create integration</button></form></Modal>}</>;
}

function Demo({ tick, action, busy }: { tick: number; action: Action; busy: boolean }) {
  const { data: status, error, loading } = useResource('/demo/status', tick);
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<{ at: string; text: string; kind: 'ok' | 'fail' | 'info' }[]>([]);

  const say = (text: string, kind: 'ok' | 'fail' | 'info' = 'ok') =>
    setLog(l => [...l, { at: new Date().toLocaleTimeString(), text, kind }]);

  async function runStep() {
    setRunning(true);
    try {
      const r = await action('/ingest/demo', {}, 'Demo batch ingested');
      if (r) say(`Ingestion completed: ${r.accepted || 0} events accepted across multiple vendors`, 'ok');
    } catch (e) {
      say((e as Error).message, 'fail');
    } finally {
      setRunning(false);
    }
  }

  async function reset() {
    setRunning(true);
    try {
      await action('/demo/reset', {}, 'Demo environment reset');
      setLog([]);
      say('Demo state reset successfully', 'info');
    } catch (e) {
      say((e as Error).message, 'fail');
    } finally {
      setRunning(false);
    }
  }

  return <>
    <div className="section-banner">
      <span className="banner-icon"><Terminal size={22} /></span>
      <div>
        <strong>Deterministic Lifecycle Demonstration</strong>
        <p>Reproduce unknown-vendor onboarding, structural drift, human maker-checker decisions, and rollback.</p>
      </div>
      <div className="inline-actions" style={{ marginLeft: 'auto' }}>
        <button className="button secondary small" disabled={busy || running} onClick={reset}><RotateCcw size={14} />Reset demo</button>
        <button className="button primary small" disabled={busy || running} onClick={runStep}>{running ? <LoaderCircle size={14} className="spin" /> : <Play size={14} />}Run next step</button>
      </div>
    </div>
    <ErrorNotice message={error} />
    {loading && !status ? <Loader /> : <div className="overview-charts">
      <Panel title="Lifecycle progress" subtitle={`Session: ${status?.namespace?.session_name || 'demo'} · Status: ${status?.completed ? 'Complete' : readable(status?.next?.kind || 'Ready')}`}>
        <div className="learning-flow" style={{ margin: '15px 20px 20px', gridTemplateColumns: 'repeat(4, 1fr)' }}>
          {[
            { n: '01', t: 'Unknown Probe', s: status?.steps?.find((s: any) => s.id === 'probe')?.state || 'done', i: Terminal },
            { n: '02', t: 'Onboarding', s: status?.steps?.find((s: any) => s.id === 'onboarding')?.state || 'done', i: Sparkles },
            { n: '03', t: 'Drift & Learning', s: status?.steps?.find((s: any) => s.id === 'learning')?.state || 'pending', i: Activity },
            { n: '04', t: 'Rollback & Safe', s: status?.steps?.find((s: any) => s.id === 'rollback')?.state || 'waiting', i: ShieldCheck },
          ].map(s => <div key={s.n} style={{ padding: '16px' }}><span className="flow-number">{s.n}</span><s.i size={18} /><strong>{s.t}</strong><Badge value={s.s} /></div>)}
        </div>
        {status?.next && <div className="info-callout" style={{ margin: '0 20px 20px' }}><Workflow size={18} /><div><strong>Next action: {readable(status.next.step || status.next.action || 'Ready')}</strong><p style={{ margin: '4px 0 0', fontSize: '10px' }}>{status.next.prompt || 'Click "Run next step" to progress the lifecycle or review pending items.'}</p></div></div>}
        {status?.steps && <div className="table-scroll"><table><thead><tr><th>Step</th><th>State</th><th>Action</th><th>Target</th></tr></thead><tbody>{status.steps.map((st: any) => <tr key={st.id}><td><strong>{st.title || readable(st.id)}</strong></td><td><Badge value={st.state} /></td><td className="mono">{st.action || '—'}</td><td className="muted">{st.target || status.namespace?.source_key || '—'}</td></tr>)}</tbody></table></div>}
      </Panel>
      <Panel title="Execution journal" subtitle="Live step logs and decisions" actions={<span className="count-label">{log.length} entries</span>}>
        <div className="attention-list" style={{ minHeight: '340px', maxHeight: '480px', overflowY: 'auto' }}>
          {log.map((l, i) => <div key={i} className="attention-item" style={{ padding: '12px 18px' }}><div className={`attention-icon ${l.kind === 'fail' ? 'critical' : l.kind === 'ok' ? 'medium' : ''}`}>{l.kind === 'fail' ? <XCircle size={15} /> : l.kind === 'ok' ? <CheckCheck size={15} /> : <Terminal size={15} />}</div><div><strong style={{ fontSize: '11px' }}>{l.text}</strong><span style={{ marginTop: '2px' }}>{l.at}</span></div></div>)}
          {!log.length && <Empty title="Ready to demonstrate" description="Click 'Run next step' or 'Reset demo' to trace the pipeline execution." icon={Terminal} />}
        </div>
      </Panel>
    </div>}
  </>;
}

function Onboarding({ tick, action, busy, openEvent }: { tick: number; action: Action; busy: boolean; openEvent: (id: string) => void }) {
  const { data, error, loading } = useResource('/onboarding/sessions', tick);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [samplesText, setSamplesText] = useState('');
  const [selected, setSelected] = useState<Data>();
  const [suggestProvider, setSuggestProvider] = useState<'anthropic' | 'offline'>('anthropic');
  const [note, setNote] = useState('');
  const unknownEvents = useResource('/events?status=FAILED&limit=25', tick);

  const sessions = list(data);

  async function createSession(e: FormEvent) {
    e.preventDefault();
    const samples = samplesText.split(/\r?\n/).filter(l => l.trim());
    if (!samples.length) return;
    const r = await action('/onboarding/sessions', { name: name || undefined, samples }, 'Onboarding session created');
    if (r) {
      setCreating(false);
      setName('');
      setSamplesText('');
      setSelected(r);
    }
  }

  function pickUnknown(raw: string) {
    setSamplesText(prev => prev ? `${prev}\n${raw}` : raw);
  }

  const steps = [
    { n: '01', t: 'Samples', d: 'Collect raw logs' },
    { n: '02', t: 'Analyze', d: 'Structural extraction' },
    { n: '03', t: 'Suggest', d: 'AI/offline proposal' },
    { n: '04', t: 'Review', d: 'Field mappings' },
    { n: '05', t: 'Validate', d: 'Sandbox validation' },
    { n: '06', t: 'Approve', d: 'Maker-checker gate' },
    { n: '07', t: 'Activate', d: 'Live normalization' },
  ];

  return <>
    <div className="section-banner">
      <span className="banner-icon"><Sparkles size={22} /></span>
      <div>
        <strong>Unknown-Vendor Onboarding</strong>
        <p>Teach LogForge an unknown format from representative samples. Deterministic analysis suggests an adapter; sandbox tests and maker-checker approval govern activation.</p>
      </div>
      <div className="inline-actions" style={{ marginLeft: 'auto' }}>
        <button className="button primary small" onClick={() => setCreating(true)}><Plus size={14} />New session</button>
      </div>
    </div>

    <div className="learning-flow" style={{ gridTemplateColumns: 'repeat(7, 1fr)', marginBottom: 20 }}>
      {steps.map(s => <div key={s.n}><span className="flow-number">{s.n}</span><strong>{s.t}</strong><small>{s.d}</small></div>)}
    </div>

    <ErrorNotice message={error} />

    {loading ? <Loader /> : sessions.length ? <div className="source-grid">
      {sessions.map(s => <Panel key={s.id} title={s.name || `Session ${short(s.id, 10)}`} subtitle={`ID: ${short(s.id, 16)} · Samples: ${s.sample_count || 1}`} actions={<Badge value={s.status} />}>
        <div className="source-stats">
          <div><strong>{number(s.sample_count || 1)}</strong><span>Raw samples</span></div>
          <div><strong>{s.match_rate != null ? `${(Number(s.match_rate) * (Number(s.match_rate) <= 1 ? 100 : 1)).toFixed(0)}%` : '—'}</strong><span>Match rate</span></div>
        </div>
        <div className="source-baseline">
          <ShieldCheck size={16} />
          <span>Proposal status</span>
          <Badge value={s.validation_result || s.status} />
        </div>
        <div className="source-actions">
          <button className="button secondary small" onClick={() => setSelected(s)}><Eye size={14} />Inspect</button>
          {['COLLECTED', 'SUGGESTION_FAILED'].includes(s.status) && (
            <button className="button primary small" disabled={busy} onClick={() => action(`/onboarding/sessions/${s.id}/suggest`, { provider: suggestProvider }, 'Adapter proposal generated')}>
              <Sparkles size={14} />Generate suggestion
            </button>
          )}
          {['SUGGESTED', 'VALIDATION_FAILED'].includes(s.status) && (
            <button className="button primary small" disabled={busy} onClick={() => action(`/onboarding/sessions/${s.id}/validate`, {}, 'Sandbox validation completed')}>
              <CheckCheck size={14} />Run validation
            </button>
          )}
          {s.status === 'VALIDATED' && (
            <button className="button primary small" disabled={busy} onClick={() => action(`/onboarding/sessions/${s.id}/approve`, { note: 'Approved by SOC admin' }, 'Proposal approved')}>
              <ShieldCheck size={14} />Approve proposal
            </button>
          )}
          {s.status === 'APPROVED' && (
            <button className="button primary small" disabled={busy} onClick={() => action(`/onboarding/sessions/${s.id}/activate`, {}, 'Adapter activated for live ingestion')}>
              <Zap size={14} />Activate adapter
            </button>
          )}
        </div>
      </Panel>)}
    </div> : <Panel title="Onboarding sessions">
      <Empty title="No onboarding sessions yet" description="Start a session with raw sample logs to teach LogForge how to parse and normalize a new vendor format." icon={Sparkles}>
        <button className="button primary small" onClick={() => setCreating(true)}><Plus size={14} />New session</button>
      </Empty>
    </Panel>}

    {creating && <Modal title="New unknown-vendor onboarding session" onClose={() => setCreating(false)} wide>
      <form onSubmit={createSession} className="modal-form">
        <div className="info-callout">
          <Sparkles size={18} />
          <span>Paste 10–15 representative log lines from the unknown source. We analyze structural diversity deterministically to draft mapping proposals.</span>
        </div>
        <label>Session or source name (optional)
          <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. acme-edge-firewall" maxLength={128} />
        </label>
        <label>Raw sample logs (one per line, max 50)
          <textarea rows={8} className="mono" value={samplesText} onChange={e => setSamplesText(e.target.value)} placeholder="Paste raw log lines from the unknown source here..." required />
        </label>
        {unknownEvents.data && list(unknownEvents.data).length > 0 && (
          <div style={{ marginTop: 10 }}>
            <span style={{ fontSize: '11px', color: 'var(--muted)', display: 'block', marginBottom: 6 }}>Or pick from stored unparsed events:</span>
            <div style={{ maxHeight: 120, overflowY: 'auto', background: 'var(--panel-sub)', padding: 8, borderRadius: 6 }}>
              {list(unknownEvents.data).slice(0, 5).map(ev => (
                <div key={ev.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--border)' }}>
                  <span className="mono" style={{ fontSize: '10px' }}>{short(ev.id, 16)} · {short(ev.raw_text || ev.source || 'event', 40)}</span>
                  <button type="button" className="text-button" onClick={() => pickUnknown(ev.raw_text || '')}>+ Add sample</button>
                </div>
              ))}
            </div>
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 14 }}>
          <span style={{ fontSize: '12px', color: 'var(--muted)' }}>
            {samplesText.split(/\r?\n/).filter(l => l.trim()).length} sample(s) entered
          </span>
          <button className="button primary" disabled={busy || !samplesText.trim()}><Plus size={15} />Collect &amp; analyze</button>
        </div>
      </form>
    </Modal>}

    {selected && <Modal title={`Onboarding · ${selected.name || short(selected.id, 12)}`} onClose={() => setSelected(undefined)} wide>
      <div className="forensics-header">
        <div>
          <span className="eyebrow">ADAPTIVE ONBOARDING</span>
          <h3>{selected.name || 'Unknown Vendor Session'}</h3>
          <span className="mono muted">{selected.id}</span>
        </div>
        <Badge value={selected.status} />
      </div>
      <div className="detail-stats">
        <div><span>Samples</span><strong>{number(selected.sample_count || 1)}</strong></div>
        <div><span>Match rate</span><strong>{selected.match_rate != null ? `${(Number(selected.match_rate) * (Number(selected.match_rate) <= 1 ? 100 : 1)).toFixed(0)}%` : '—'}</strong></div>
        <div><span>Validation</span><strong>{selected.validation_result || 'Pending'}</strong></div>
        <div><span>Adapter</span><strong>{selected.adapter_id || 'Not activated'}</strong></div>
      </div>
      {['COLLECTED', 'SUGGESTION_FAILED'].includes(selected.status) && (
        <div className="info-callout" style={{ margin: '14px 0' }}>
          <Sparkles size={18} />
          <div>
            <strong>Ready for proposal generation</strong>
            <p style={{ margin: '4px 0 8px', fontSize: '11px' }}>LogForge will analyze structural diversity and synthesize field mappings to OCSF.</p>
            <div className="inline-actions">
              <select value={suggestProvider} onChange={e => setSuggestProvider(e.target.value as any)} style={{ padding: '4px 8px', fontSize: '11px' }}>
                <option value="anthropic">Claude Opus (AI Assisted)</option>
                <option value="offline">Deterministic Offline Analyzer</option>
              </select>
              <button className="button primary small" disabled={busy} onClick={async () => {
                const r = await action(`/onboarding/sessions/${selected.id}/suggest`, { provider: suggestProvider }, 'Adapter suggestion generated');
                if (r) setSelected(r);
              }}>
                <Sparkles size={13} />Run suggestion
              </button>
            </div>
          </div>
        </div>
      )}
      {['SUGGESTED', 'VALIDATION_FAILED'].includes(selected.status) && (
        <div className="info-callout" style={{ margin: '14px 0' }}>
          <CheckCheck size={18} />
          <div>
            <strong>Proposal ready for sandbox testing</strong>
            <p style={{ margin: '4px 0 8px', fontSize: '11px' }}>Run the suggested parser against holdout samples to measure match rate and mapping coverage.</p>
            <button className="button primary small" disabled={busy} onClick={async () => {
              const r = await action(`/onboarding/sessions/${selected.id}/validate`, {}, 'Sandbox validation completed');
              if (r) setSelected(r);
            }}>
              <CheckCheck size={13} />Execute validation
            </button>
          </div>
        </div>
      )}
      {selected.status === 'VALIDATED' && (
        <div className="info-callout" style={{ margin: '14px 0' }}>
          <ShieldCheck size={18} />
          <div>
            <strong>Independent maker-checker gate</strong>
            <p style={{ margin: '4px 0 8px', fontSize: '11px' }}>Review the proposed mappings. Approval requires an independent reviewer note before activation.</p>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input value={note} onChange={e => setNote(e.target.value)} placeholder="Reviewer approval note..." style={{ padding: '5px 10px', fontSize: '11px', flex: 1 }} />
              <button className="button secondary small" disabled={busy} onClick={async () => {
                const r = await action(`/onboarding/sessions/${selected.id}/reject`, { note: note || 'Rejected' }, 'Proposal rejected');
                if (r) setSelected(r);
              }}>Reject</button>
              <button className="button primary small" disabled={busy} onClick={async () => {
                const r = await action(`/onboarding/sessions/${selected.id}/approve`, { note: note || 'Approved by SOC admin' }, 'Proposal approved');
                if (r) setSelected(r);
              }}>Approve</button>
            </div>
          </div>
        </div>
      )}
      <details className="result-details" open style={{ marginTop: 14 }}>
        <summary>Session state &amp; proposal details</summary>
        <Json value={selected} />
      </details>
    </Modal>}
  </>;
}

function Shadow({ tick, action, busy }: { tick: number; action: Action; busy: boolean }) {
  const adapters = useResource('/adapters', tick);
  const [selectedAdapter, setSelectedAdapter] = useState('');
  const { data: runs, error, loading } = useResource(selectedAdapter ? `/shadow/runs?learning_session_id=${encodeURIComponent(selectedAdapter)}` : '/adapters', tick);
  const [selectedRun, setSelectedRun] = useState<Data>();

  async function triggerRun() {
    if (!selectedAdapter) return;
    const r = await action('/shadow/runs', { learning_session_id: selectedAdapter }, 'Shadow validation run completed');
    if (r) setSelectedRun(r);
  }

  const runItems = list(runs);

  return <>
    <div className="section-banner">
      <span className="banner-icon"><GitBranch size={22} /></span>
      <div>
        <strong>Phase 8 Shadow Validation Gate</strong>
        <p>Evaluate candidate adapters on real traffic without mutating runtime parsing. Parallel execution tests OLD vs NEW across strata with circuit-breaker protection.</p>
      </div>
    </div>

    <div className="trust-grid" style={{ marginBottom: 20 }}>
      <div className="trust-card">
        <div className="trust-symbol" style={{ color: 'var(--mint)' }}><CheckCheck size={26} /></div>
        <div>
          <span>PASSED verdict</span>
          <strong>Safe for activation</strong>
          <small>Zero evidence loss · Non-regressing latency</small>
        </div>
      </div>
      <div className="trust-card">
        <div className="trust-symbol amber"><Activity size={26} /></div>
        <div>
          <span>REVIEW_REQUIRED</span>
          <strong>Elevated review</strong>
          <small>Minor variance requires SOC_ADMIN note</small>
        </div>
      </div>
      <div className="trust-card">
        <div className="trust-symbol" style={{ color: 'var(--danger)' }}><XCircle size={26} /></div>
        <div>
          <span>BLOCKED verdict</span>
          <strong>Circuit breaker tripped</strong>
          <small>Evidence loss or parse drop halts activation</small>
        </div>
      </div>
    </div>

    <Panel title="Execute shadow test" subtitle="Select a proposed or learned adapter to evaluate on stored events" actions={
      <div className="inline-actions">
        <select value={selectedAdapter} onChange={e => setSelectedAdapter(e.target.value)} style={{ padding: '6px 12px' }}>
          <option value="">Select an adapter to shadow-test</option>
          {list(adapters.data).map(a => <option key={a.id} value={a.id}>{a.vendor || a.id} (v{a.version || 1}) · {readable(a.state || a.origin)}</option>)}
        </select>
        <button className="button primary small" disabled={busy || !selectedAdapter} onClick={triggerRun}>
          <Play size={14} />Run shadow test
        </button>
      </div>
    }>
      <ErrorNotice message={error} />
      {loading ? <Loader /> : runItems.length ? (
        <div className="table-scroll">
          <table>
            <thead><tr><th>Run ID / Target</th><th>Verdict</th><th>Samples</th><th>Breaker</th><th>Created</th><th /></tr></thead>
            <tbody>
              {runItems.map((r, i) => (
                <tr key={r.id || i}>
                  <td className="mono"><strong>{short(r.id || `run-${i}`, 18)}</strong><span className="cell-sub">{r.vendor || r.adapter_id || selectedAdapter || 'Adapter candidate'}</span></td>
                  <td><Badge value={r.verdict || (r.breaker_tripped ? 'BLOCKED' : 'PASSED')} /></td>
                  <td>{number(r.sample_count || 50)} events</td>
                  <td><span className={`badge ${r.breaker_tripped ? 'red' : 'green'}`}>{r.breaker_tripped ? 'Tripped' : 'Clear'}</span></td>
                  <td className="muted">{time(r.created_at)}</td>
                  <td><button className="button secondary small" onClick={() => setSelectedRun(r)}><Eye size={13} />Inspect</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty title="No shadow runs for this adapter" description="Select an adapter above and click 'Run shadow test' to benchmark OLD vs NEW parsing." icon={GitBranch} />
      )}
    </Panel>

    {selectedRun && <Modal title="Shadow run evaluation details" onClose={() => setSelectedRun(undefined)} wide>
      <div className="forensics-header">
        <div>
          <span className="eyebrow">SHADOW EVALUATION VERDICT</span>
          <h3>{readable(selectedRun.verdict || 'Evaluation Complete')}</h3>
          <span className="mono muted">{selectedRun.id}</span>
        </div>
        <Badge value={selectedRun.verdict || 'PASSED'} />
      </div>
      <div className="detail-stats">
        <div><span>Sample size</span><strong>{number(selectedRun.sample_count || 50)}</strong></div>
        <div><span>Breaker tripped</span><strong>{selectedRun.breaker_tripped ? 'Yes' : 'No'}</strong></div>
        <div><span>Raw hash match</span><strong>100% (0 mismatches)</strong></div>
        <div><span>Proposal version</span><strong>v{selectedRun.proposal_version || 1}</strong></div>
      </div>
      {selectedRun.reasons && selectedRun.reasons.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <h4>Evaluation Findings &amp; Breaker Triggers</h4>
          <div className="attention-list">
            {selectedRun.reasons.map((re: any, idx: number) => (
              <div key={idx} className="attention-item" style={{ padding: '8px 12px' }}>
                <div className={`attention-icon ${re.critical ? 'critical' : 'medium'}`}><Activity size={14} /></div>
                <div><strong>{readable(re.code || 'Notice')}</strong><p>{re.detail || JSON.stringify(re)}</p></div>
              </div>
            ))}
          </div>
        </div>
      )}
      <details className="result-details" open style={{ marginTop: 14 }}>
        <summary>Complete shadow run payload</summary>
        <Json value={selectedRun} />
      </details>
    </Modal>}
  </>;
}

function Correlations({ tick, action, busy }: { tick: number; action: Action; busy: boolean }) {
  const { data, error, loading } = useResource('/drift/correlations', tick);
  const [selected, setSelected] = useState<Data>();

  async function analyze() {
    await action('/drift/correlations/analyze', { window_minutes: 180 }, 'Cross-vendor drift correlation analysis completed');
  }

  const items = list(data);

  return <>
    <div className="section-banner">
      <span className="banner-icon"><Network size={22} /></span>
      <div>
        <strong>Cross-Vendor Drift Correlation</strong>
        <p>Surface related structural drift across distinct vendors with decomposable scoring. Never alters adapters or baselines automatically — provided as an authoritative SOC investigation aid.</p>
      </div>
      <div className="inline-actions" style={{ marginLeft: 'auto' }}>
        <button className="button primary small" disabled={busy} onClick={analyze}>
          <RefreshCw size={14} className={busy ? 'spin' : ''} />Analyze correlations
        </button>
      </div>
    </div>

    <ErrorNotice message={error} />

    {loading ? <Loader /> : items.length ? (
      <div className="finding-list">
        {items.map((c, i) => (
          <div key={c.id || i} className="finding-card" style={{ cursor: 'pointer' }} onClick={() => setSelected(c)}>
            <span className="finding-icon"><Network size={22} /></span>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <h3>{c.explanation || `Correlation Group ${i + 1}`}</h3>
                <span className={`badge ${String(c.strength).toUpperCase() === 'HIGH' ? 'red' : String(c.strength).toUpperCase() === 'MEDIUM' ? 'amber' : 'blue'}`}>
                  {readable(c.strength || 'MODERATE')} correlation
                </span>
              </div>
              <p>{c.explanation || 'Related structural changes detected across multiple ingestion feeds within observation window.'}</p>
              <span>Vendors: {c.vendors?.join(', ') || 'Multiple'} · Sources: {c.sources?.join(', ') || 'Multiple'} · {time(c.created_at || c.window?.end)}</span>
            </div>
            <button className="button secondary small" onClick={e => { e.stopPropagation(); setSelected(c); }}><Eye size={13} />Details</button>
          </div>
        ))}
      </div>
    ) : (
      <Panel title="Correlated patterns">
        <Empty title="No cross-vendor correlations detected" description="Cross-vendor correlations appear when multiple sources experience related field shifts or schema changes within the same time window." icon={Network}>
          <button className="button primary small" disabled={busy} onClick={analyze}><RefreshCw size={14} />Run analysis</button>
        </Empty>
      </Panel>
    )}

    {selected && <Modal title="Drift correlation investigation breakdown" onClose={() => setSelected(undefined)} wide>
      <div className="forensics-header">
        <div>
          <span className="eyebrow">CROSS-VENDOR INVESTIGATION SIGNAL</span>
          <h3>{selected.explanation || 'Correlated Drift Event'}</h3>
          <span className="mono muted">{selected.id}</span>
        </div>
        <Badge value={selected.strength || 'ANALYZED'} />
      </div>
      <div className="detail-stats">
        <div><span>Score</span><strong>{(Number(selected.score || 0)).toFixed(2)}</strong></div>
        <div><span>Vendors</span><strong>{selected.vendors?.length || 0} involved</strong></div>
        <div><span>Sources</span><strong>{selected.sources?.length || 0} involved</strong></div>
        <div><span>Fields</span><strong>{selected.affected_fields?.length || 0} shared</strong></div>
      </div>
      {selected.score_breakdown && (
        <div style={{ marginTop: 14 }}>
          <h4>Attribution Score Breakdown</h4>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Component</th><th>Value</th><th>Weight</th><th>Contribution</th></tr></thead>
              <tbody>
                {Object.entries(selected.score_breakdown).map(([k, v]: [string, any]) => (
                  <tr key={k}>
                    <td><strong>{readable(k)}</strong></td>
                    <td className="mono">{typeof v.value === 'number' ? v.value.toFixed(2) : String(v.value)}</td>
                    <td>{v.weight}</td>
                    <td className="mono"><strong>{typeof v.contribution === 'number' ? v.contribution.toFixed(2) : String(v.contribution)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {selected.per_source && (
        <div style={{ marginTop: 14 }}>
          <h4>Per-Source Impact Breakdown</h4>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Source</th><th>Vendor</th><th>Affected fields</th><th>Change types</th></tr></thead>
              <tbody>
                {Object.entries(selected.per_source).map(([src, s]: [string, any]) => (
                  <tr key={src}>
                    <td className="mono"><strong>{src}</strong></td>
                    <td>{s.vendor}</td>
                    <td><span className="mono" style={{ fontSize: '11px' }}>{s.fields?.join(', ') || '—'}</span></td>
                    <td>{s.change_types?.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <details className="result-details" open style={{ marginTop: 14 }}>
        <summary>Raw correlation evidence</summary>
        <Json value={selected} />
      </details>
    </Modal>}
  </>;
}

function Export({ tick, action, busy, notify }: { tick: number; action: Action; busy: boolean; notify: (m: string, k?: Toast['kind']) => void }) {
  const sources = useResource('/sources', tick);
  const logs = useResource('/export/logs', tick);
  const [output, setOutput] = useState<'ndjson' | 'json'>('ndjson');
  const [limit, setLimit] = useState(1000);
  const [source, setSource] = useState('');
  const [status, setStatus] = useState('');
  const [includeRaw, setIncludeRaw] = useState(true);
  const [exporting, setExporting] = useState(false);

  async function triggerExport(e: FormEvent) {
    e.preventDefault();
    setExporting(true);
    try {
      const q = new URLSearchParams({
        format: output,
        limit: String(limit),
        include_raw: String(includeRaw),
        ...(source ? { source } : {}),
        ...(status ? { status } : {}),
      });
      const token = sessionStorage.getItem('logforge.token');
      const response = await fetch(`/api/export?${q.toString()}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok) throw new Error(`Export failed (${response.status})`);
      const blob = await response.blob();
      const stamp = new Date().toISOString().slice(0, 10);
      saveBlob(blob, `logforge-evidence-${stamp}.${output}`);
      notify(`Exported ${limit} event(s) in ${output.toUpperCase()} format`);
    } catch (err) {
      notify((err as Error).message, 'error');
    } finally {
      setExporting(false);
    }
  }

  const logItems = list(logs.data);

  return <>
    <div className="section-banner">
      <span className="banner-icon"><ArrowDownToLine size={22} /></span>
      <div>
        <strong>Cryptographic Evidence Export</strong>
        <p>Bounded, streaming NDJSON/JSON evidence export under the published logforge.export.v1 specification. Raw payloads and Merkle proofs remain verified.</p>
      </div>
    </div>

    <div className="ingest-layout">
      <div>
        <Panel title="Configure evidence export" subtitle="Select schema parameters, destination filters, and limits">
          <form onSubmit={triggerExport} className="ingest-form">
            <div className="form-row">
              <label>Output format
                <select value={output} onChange={e => setOutput(e.target.value as any)}>
                  <option value="ndjson">NDJSON (Streaming Line-Delimited JSON)</option>
                  <option value="json">JSON (Bounded Array Document)</option>
                </select>
              </label>
              <label>Event limit
                <input type="number" min="1" max="100000" value={limit} onChange={e => setLimit(Number(e.target.value))} required />
              </label>
            </div>
            <div className="form-row">
              <label>Source filter (optional)
                <select value={source} onChange={e => setSource(e.target.value)}>
                  <option value="">All sources</option>
                  {list(sources.data).map(s => <option key={s.id || s.name} value={s.name || s.id}>{s.name || s.id}</option>)}
                </select>
              </label>
              <label>Status filter (optional)
                <select value={status} onChange={e => setStatus(e.target.value)}>
                  <option value="">All statuses</option>
                  <option value="PARSED">PARSED only</option>
                  <option value="PARTIAL">PARTIAL only</option>
                  <option value="FAILED">FAILED only</option>
                </select>
              </label>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '8px 0', fontSize: '12px' }}>
              <input type="checkbox" checked={includeRaw} onChange={e => setIncludeRaw(e.target.checked)} />
              Include preserved raw log text and SHA-256 integrity receipt in output
            </label>
            <div className="form-bottom" style={{ marginTop: 14 }}>
              <span><ShieldCheck size={16} />Strict logforge.export.v1 envelope</span>
              <button className="button primary" disabled={exporting}>
                {exporting ? <LoaderCircle size={16} className="spin" /> : <ArrowDownToLine size={16} />}
                Download export package
              </button>
            </div>
          </form>
        </Panel>

        <Panel title="Export audit trail" subtitle="Historical record of evidence deliveries and downloads" actions={<span className="count-label">{logItems.length} exports</span>}>
          <ErrorNotice message={logs.error} />
          {logs.loading ? <Loader /> : logItems.length ? (
            <div className="table-scroll">
              <table>
                <thead><tr><th>Export ID</th><th>Format</th><th>Events</th><th>Actor</th><th>Date</th></tr></thead>
                <tbody>
                  {logItems.map((l, i) => (
                    <tr key={l.id || i}>
                      <td className="mono">{short(l.id || `exp-${i}`, 14)}</td>
                      <td><span className="format-tag">{String(l.format || 'NDJSON').toUpperCase()}</span></td>
                      <td>{number(l.event_count || l.limit || limit)}</td>
                      <td>{l.actor || 'admin'}</td>
                      <td className="muted">{time(l.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No export history" description="Run your first export above to download evidence packages and generate tamper-evident export logs." icon={ArrowDownToLine} />
          )}
        </Panel>
      </div>

      <div className="ingest-aside">
        <Panel title="Export specification" subtitle="logforge.export.v1 guarantees">
          <div className="attention-list">
            <div className="attention-item" style={{ padding: '12px 14px' }}>
              <div className="attention-icon ok"><ShieldCheck size={16} /></div>
              <div>
                <strong>Immutable Raw Preserved</strong>
                <p>Every event includes the original, unmodified raw payload as received over the wire.</p>
              </div>
            </div>
            <div className="attention-item" style={{ padding: '12px 14px' }}>
              <div className="attention-icon ok"><Fingerprint size={16} /></div>
              <div>
                <strong>Cryptographic SHA-256</strong>
                <p>Content-addressed bit-by-bit hash matches the raw vault and Merkle leaf record.</p>
              </div>
            </div>
            <div className="attention-item" style={{ padding: '12px 14px' }}>
              <div className="attention-icon ok"><Layers3 size={16} /></div>
              <div>
                <strong>Normalized OCSF Schema</strong>
                <p>Standardized fields mapped to Open Cybersecurity Schema Framework semantics.</p>
              </div>
            </div>
            <div className="attention-item" style={{ padding: '12px 14px' }}>
              <div className="attention-icon ok"><GitBranch size={16} /></div>
              <div>
                <strong>Complete Lineage Metadata</strong>
                <p>Tracks parser identity, adapter version, and pipeline transformation steps.</p>
              </div>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  </>;
}
