export type Data = Record<string, any>;
const API = '/api';

export async function api<T = Data>(path: string, body?: unknown, method?: string): Promise<T> {
  const token = sessionStorage.getItem('logforge.token');
  const response = await fetch(`${API}${path}`, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('logforge:expired'));
    const detail = data.detail || data.message || `Request failed (${response.status})`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data as T;
}

export async function exportEvents(format: 'json' | 'ndjson') {
  const response = await fetch(`${API}/export?format=${format}`, { headers: { Authorization: `Bearer ${sessionStorage.getItem('logforge.token') || ''}` } });
  if (!response.ok) throw new Error(`Export failed (${response.status})`);
  saveBlob(await response.blob(), `logforge-evidence-${new Date().toISOString().slice(0, 10)}.${format}`);
}

export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
