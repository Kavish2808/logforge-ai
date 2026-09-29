// Hand-off from the top-bar search to the Event Explorer (which owns the real, server-side search filter).
const EVENT = "logforge:search";
let pending: string | null = null;

export const ULID = /^[0-9A-HJKMNP-TV-Z]{26}$/i;

export function requestSearch(text: string): void {
  pending = text;
  window.dispatchEvent(new CustomEvent(EVENT, { detail: text }));
}

export function takePendingSearch(): string | null {
  const t = pending;
  pending = null;
  return t;
}

export function onSearch(fn: (text: string) => void): () => void {
  const h = (e: Event) => { pending = null; fn((e as CustomEvent<string>).detail); };
  window.addEventListener(EVENT, h);
  return () => window.removeEventListener(EVENT, h);
}
