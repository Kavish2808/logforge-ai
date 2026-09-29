// One function per backend endpoint the console uses.
import { HEALTH_URL, Query, download, request } from "./client";
import type {
  Alert, AlertCounts, AlertPage, AuditPage, AuditVerify, AuthStatus, Batch, ChainVerify, ConfidenceEntry, EventVerify,
  ExportLog, ExtensionView, GovConfig, LoginResult, Me, OverflowSignature, PolicyRule, RawRecovery, Reviews, TrustSummary, User,
  Baseline, DemoFixtures, DemoReset, DemoStatus, EventPage, FilterValues, Health, LearningSession, LearningSummary, Lineage, OnboardingSession,
  OnboardingSummary, SourceDetail, SourceSummary, Summary, TimelineEntry, UniversalEvent,
} from "./types";

type Sig = AbortSignal | undefined;

// --- read-only views ---------------------------------------------------------------------
export const getHealth = (signal?: Sig) => request<Health>(HEALTH_URL, { absolute: true, signal });
export const getSummary = (query: Query, signal?: Sig) => request<Summary>("/views/summary", { query, signal });
export const getFilters = (signal?: Sig) => request<FilterValues>("/views/filters", { signal });
export const getEvents = (query: Query, signal?: Sig) => request<EventPage>("/views/events", { query, signal });
export const getLineage = (id: string, signal?: Sig) =>
  request<Lineage>(`/views/events/${encodeURIComponent(id)}/lineage`, { signal });
export const getSources = (signal?: Sig) => request<{ total: number; items: SourceSummary[] }>("/views/sources", { signal });
export const getSource = (key: string, signal?: Sig) =>
  request<SourceDetail>(`/views/sources/${encodeURIComponent(key)}`, { signal });
export const getTimeline = (key: string, signal?: Sig) =>
  request<{ source_key: string; entries: TimelineEntry[] }>(`/views/sources/${encodeURIComponent(key)}/timeline`, { signal });

// --- existing Phase 0-6 APIs ---------------------------------------------------------------
export const getEvent = (id: string, signal?: Sig) =>
  request<UniversalEvent>(`/events/${encodeURIComponent(id)}`, { signal });
export const getBaseline = (key: string, signal?: Sig) =>
  request<Baseline>(`/drift/baselines/${encodeURIComponent(key)}`, { signal });
export const acceptDrift = (id: string, mode: string, note: string | null) =>
  request<{ event: UniversalEvent }>(`/events/${encodeURIComponent(id)}/drift/accept`, { method: "POST", body: { mode, note } });

export const listOnboarding = (signal?: Sig) =>
  request<{ total: number; items: OnboardingSummary[] }>("/onboarding/sessions", { query: { limit: 100 }, signal });
export const getOnboarding = (id: string, signal?: Sig) =>
  request<OnboardingSession>(`/onboarding/sessions/${encodeURIComponent(id)}`, { signal });
export const createOnboarding = (body: { name?: string | null; samples: string[]; event_ids: string[] }) =>
  request<OnboardingSession>("/onboarding/sessions", { method: "POST", body });
export const suggestOnboarding = (id: string, provider: string) =>
  request<OnboardingSession>(`/onboarding/sessions/${encodeURIComponent(id)}/suggest`, { method: "POST", body: { provider } });
export const approveOnboarding = (id: string, body: { proposal_version: number; approved_by: string | null; note: string | null; adapter_id?: string }) =>
  request<{ session: OnboardingSession }>(`/onboarding/sessions/${encodeURIComponent(id)}/approve`, { method: "POST", body });
export const rejectOnboarding = (id: string, body: { reason: string; rejected_by: string | null }) =>
  request<OnboardingSession>(`/onboarding/sessions/${encodeURIComponent(id)}/reject`, { method: "POST", body });
export const rollbackOnboardedAdapter = (adapterId: string, reason: string | null) =>
  request(`/onboarding/adapters/${encodeURIComponent(adapterId)}/rollback`, { method: "POST", body: { reason } });

export const listLearning = (query: Query, signal?: Sig) =>
  request<{ total: number; items: LearningSummary[] }>("/learning/sessions", { query, signal });
export const getLearning = (id: string, signal?: Sig) =>
  request<LearningSession>(`/learning/sessions/${encodeURIComponent(id)}`, { signal });
export const proposeLearning = (eventId: string, assistant: "auto" | "offline" = "auto", requestedBy: string | null = null) =>
  request<LearningSession>(`/events/${encodeURIComponent(eventId)}/learning/propose`, { method: "POST", body: { assistant, requested_by: requestedBy } });
export const learningAction = (id: string, action: string, body: object = {}) =>
  request<LearningSession>(`/learning/sessions/${encodeURIComponent(id)}/${action}`, { method: "POST", body });

// Ingest one raw log through the normal pipeline (the demo uses it exactly like any client).
export const ingest = (rawLog: string) => request<UniversalEvent>("/ingest", { method: "POST", body: { raw_log: rawLog } });

// --- Demo Mode ------------------------------------------------------------------------------
export const getDemoFixtures = (signal?: Sig) => request<DemoFixtures>("/demo/fixtures", { signal });
export const getDemoStatus = (signal?: Sig) => request<DemoStatus>("/demo/status", { signal });
export const resetDemo = () => request<DemoReset>("/demo/reset", { method: "POST" });

// --- Phase 7: trust / governance / integration ----------------------------------------------
const enc = encodeURIComponent;
export const getTrust = (signal?: Sig) => request<TrustSummary>("/views/trust", { signal });

export const getAuthStatus = (signal?: Sig) => request<AuthStatus>("/auth/status", { signal });
export const getMe = (signal?: Sig) => request<Me>("/auth/me", { signal });
export const login = (username: string, password: string) =>
  request<LoginResult>("/auth/login", { method: "POST", body: { username, password } });
export const logout = () => request("/auth/logout", { method: "POST" });
export const bootstrapAdmin = (username: string, password: string) =>
  request<User>("/auth/bootstrap", { method: "POST", body: { username, password } });
export const listUsers = (signal?: Sig) => request<{ total: number; items: User[] }>("/auth/users", { signal });
export const createUser = (body: { username: string; password: string; role: string }) =>
  request<User>("/auth/users", { method: "POST", body });
export const updateUser = (username: string, body: { role?: string; active?: boolean }) =>
  request<User>(`/auth/users/${enc(username)}`, { method: "PATCH", body });

export const getConfig = (signal?: Sig) => request<GovConfig>("/governance/config", { signal });
export const putConfig = (body: { review_sla?: object; alert_thresholds?: object }) =>
  request<GovConfig>("/governance/config", { method: "PUT", body });
export const getReviews = (query: Query, signal?: Sig) => request<Reviews>("/governance/reviews", { query, signal });
export const getAudit = (query: Query, signal?: Sig) => request<AuditPage>("/governance/audit", { query, signal });
export const verifyAudit = () => request<AuditVerify>("/governance/audit/verify");
export const getPolicy = (signal?: Sig) =>
  request<{ rbac_mode: string; roles: Record<string, string[]>; rules: PolicyRule[] }>("/governance/policy", { signal });

export const verifyChain = () => request<ChainVerify>("/integrity/verify");
export const sealNow = (force: boolean) => request<{ sealed_batches: Batch[] }>("/integrity/seal", { method: "POST", body: { force } });
export const getBatches = (signal?: Sig) => request<{ items: Batch[] }>("/integrity/batches", { query: { limit: 20 }, signal });
export const verifyEvent = (id: string) => request<EventVerify>(`/integrity/events/${enc(id)}`);
export const recoverRaw = (id: string) => request<RawRecovery>(`/integrity/raw/${enc(id)}/recover`);
export const backfillVault = () => request<{ archived: number; failed: number }>("/integrity/raw/backfill", { method: "POST" });
export const getExtensions = (id: string, signal?: Sig) => request<ExtensionView>(`/integrity/extensions/${enc(id)}`, { signal });
export const getOverflowEvidence = (signal?: Sig) => request<{ items: OverflowSignature[] }>("/integrity/overflow/evidence", { signal });
export const overflowToOnboarding = (id: number) =>
  request<{ onboarding_session_id: string }>(`/integrity/overflow/evidence/${id}/onboarding`, { method: "POST" });

export const getAlerts = (query: Query, signal?: Sig) => request<AlertPage>("/alerts", { query, signal });
export const getAlertCounts = (signal?: Sig) => request<AlertCounts>("/alerts/counts", { signal });
export const ackAlert = (id: string, note: string | null) => request<Alert>(`/alerts/${enc(id)}/ack`, { method: "POST", body: { note } });
export const markAlertRead = (id: string, unread = false) => request<Alert>(`/alerts/${enc(id)}/read`, { method: "POST", query: { unread } });
export const sweepAlerts = () => request("/alerts/sweep", { method: "POST" });
export const getAlertChannels = (signal?: Sig) =>
  request<{ channels: { channel: string; configured: boolean }[] }>("/alerts/channels", { signal });

export const getConfidence = (kind: "onboarding" | "learning", id: string, signal?: Sig) =>
  request<{ entries: ConfidenceEntry[]; note: string }>(`/confidence/${kind}/${enc(id)}`, { signal });

export const getExportSchema = (signal?: Sig) =>
  request<{ schema_version: string; semantics: Record<string, string> }>("/export/schema", { signal });
export const getExportLogs = (signal?: Sig) => request<{ items: ExportLog[] }>("/export/logs", { query: { limit: 10 }, signal });
export const exportEvents = (query: Query) => download("/export/events", { query });

// --- Integrations registry (SOC_ADMIN; delivery answers 501 NOT_IMPLEMENTED) ------------------
export const listIntegrations = (signal?: Sig) => request<import("./types").IntegrationItem[]>("/integrations", { signal });
export const createIntegration = (body: { name: string; url: string }) =>
  request<import("./types").IntegrationItem>("/integrations", { method: "POST", body });
