// One function per backend endpoint the console uses.
import { HEALTH_URL, Query, request } from "./client";
import type {
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
