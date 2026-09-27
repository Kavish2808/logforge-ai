// Signed-in identity for Phase 7 RBAC. The bearer token lives in sessionStorage
// (cleared when the tab closes); storage failures degrade to "signed out".
import { useSyncExternalStore } from "react";

export interface AuthUser {
  username: string;
  role: string;
  capabilities: string[];
}

interface AuthState {
  token: string | null;
  user: AuthUser | null;
}

const KEY = "logforge.auth";
let state: AuthState = load();
const listeners = new Set<() => void>();

function load(): AuthState {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as AuthState;
  } catch {
    // storage unavailable or corrupt: signed out
  }
  return { token: null, user: null };
}

function save(next: AuthState): void {
  state = next;
  try {
    if (next.token) sessionStorage.setItem(KEY, JSON.stringify(next));
    else sessionStorage.removeItem(KEY);
  } catch {
    // keep the in-memory state only
  }
  listeners.forEach((l) => l());
}

export const getToken = (): string | null => state.token;
export const setAuth = (token: string, user: AuthUser): void => save({ token, user });
export const clearAuth = (): void => save({ token: null, user: null });

export function useAuth(): AuthState {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => listeners.delete(l); },
    () => state,
  );
}

export function can(user: AuthUser | null, capability: string): boolean {
  return !!user && user.capabilities.includes(capability);
}
