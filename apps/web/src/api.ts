/** Typed client for the NODO CORE API. Token (optional) is read from localStorage "nodo_token". */

export type NodoEvent = { type: string; request_id: string; at: string; [k: string]: unknown };
/** Realtime voice protocol event (nodo/voice/protocol.py). */
export type VoiceEvent = { type: string; session_id: string; turn: number; seq: number; [k: string]: unknown };
export type Project = { id: string; name: string; kind: string; status: string; priority: number; client_id: string | null };
export type Task = { id: string; title: string; status: string; priority: number; due_date: string | null; project_id: string | null };
export type Usage = { local_requests: number; cloud_requests: number; premium_requests: number; agent_runs: number; tool_executions: number; tokens: number; estimated_cost: number };
export type SystemInfo = { mode: string; env: string; models: { id: string; tier: string; is_local: boolean; available: boolean }[]; voice: { stt: { name: string; location: string }; tts: { name: string; location: string } }; github: string; usage: Usage };
export type VoiceConfig = { stt: { name: string; location: string }; tts: { name: string; location: string; voice: string }; language: string };

const headers = (): Record<string, string> => {
  const t = localStorage.getItem("nodo_token");
  return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
};

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: headers() });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: headers(), body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

/** POST /api/v1/command and parse the SSE stream. Calls onEvent for every event; resolves on `done`/`error`. */
export async function streamCommand(
  body: { text: string; conversation_id?: string | null; channel?: string },
  onEvent: (e: NodoEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch("/api/v1/command", { method: "POST", headers: headers(), body: JSON.stringify(body), signal });
  if (!r.ok || !r.body) throw new Error(`command: ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const data = chunk.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
      if (data) onEvent(JSON.parse(data));
    }
  }
}

export async function transcribe(input: { blob?: Blob; text?: string; sessionId?: string }): Promise<{ text: string; provider: string }> {
  const fd = new FormData();
  if (input.blob) fd.append("file", input.blob, "speech.webm");
  if (input.text !== undefined) fd.append("text", input.text);
  if (input.sessionId) fd.append("session_id", input.sessionId);
  const t = localStorage.getItem("nodo_token");
  const r = await fetch("/api/v1/voice/transcribe", { method: "POST", body: fd, headers: t ? { Authorization: `Bearer ${t}` } : {} });
  if (!r.ok) throw new Error(`transcribe: ${r.status}`);
  return r.json();
}

/** WebSocket URL for the realtime voice channel (token via query param: browsers can't set WS headers). */
export function voiceWsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const t = localStorage.getItem("nodo_token");
  return `${proto}://${location.host}/api/v1/voice/stream${t ? `?token=${encodeURIComponent(t)}` : ""}`;
}

export class VoiceSocket {
  private ws: WebSocket;
  constructor(onEvent: (e: VoiceEvent) => void, onClose: (e: CloseEvent) => void) {
    this.ws = new WebSocket(voiceWsUrl());
    this.ws.onmessage = (m) => onEvent(JSON.parse(m.data as string));
    this.ws.onclose = onClose;
  }
  send(o: Record<string, unknown>): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(o));
  }
  close(): void { this.ws.close(); }
}

export async function speak(text: string): Promise<{ mime: string; data: Blob }> {
  const fd = new FormData();
  fd.append("text", text);
  const t = localStorage.getItem("nodo_token");
  const r = await fetch("/api/v1/voice/speak", { method: "POST", body: fd, headers: t ? { Authorization: `Bearer ${t}` } : {} });
  if (!r.ok) throw new Error(`speak: ${r.status}`);
  return { mime: r.headers.get("content-type") || "", data: await r.blob() };
}

// ------------------------------------------------------------------ night shift (docs/NIGHT_SHIFT.md)
export type SiteStatus = "ok" | "warning" | "critical" | "unknown";
export type KeyPage = { path: string; expect?: string | null; name?: string | null };
export type Site = {
  id: string; name: string; url: string; status: SiteStatus; status_note: string | null; last_checked_at: string | null;
  vercel_project_id: string | null; repo_owner: string | null; repo_name: string | null; default_branch: string;
  key_pages: KeyPage[]; policy: Record<string, boolean>; active: boolean; open_incidents: number;
  last_good_deployment_id: string | null; current_deployment_id: string | null;
};
export type SiteCheck = { id: string; check: string; status: string; summary: string; created_at: string };
export type Incident = {
  id: string; site_id: string; site?: string; kind: string; severity: "critical" | "warning" | "info"; title: string;
  status: string; resolution: string | null; occurrences: number; first_seen_at: string; last_seen_at: string;
};
export type NightAction = { id: string; site?: string | null; action: string; status: string; summary: string; created_at: string };
export type SiteDetail = Site & { checks: SiteCheck[]; incidents: Incident[]; actions: NightAction[] };
export type Pending = {
  approval_id: string; label: string; tool: string; action: string; fix_id: string | null; site: string | null;
  pr_url: string | null; preview_url: string | null; preview_status: string | null; created_at: string;
};
export type BriefingItem = {
  site: string; site_id: string; kind: string; severity: string; title: string; status: string; resolution: string | null;
  first_seen: string; actions: { action: string; status: string; summary: string; at: string }[];
  fix: { id: string; pr_number: number | null; pr_url: string | null; preview_status: string; preview_url: string | null; status: string } | null;
};
export type Briefing = {
  text: string; generated_at: string; runs: number; sites_checked: number; sites_total: number; items: BriefingItem[];
  pending: Pending[]; drafts: { summary: string }[]; regular: string[]; cost_usd: number; stored?: boolean;
};
export type Fix = {
  id: string; site: string | null; site_id: string; incident_title: string | null; title: string; diagnosis: string;
  confidence: string; files: { path: string; added: number; removed: number }[]; diff: string; branch: string | null;
  pr_number: number | null; pr_url: string | null; preview_url: string | null; preview_status: string;
  preview_note: string | null; model: string | null; status: string; approval_id: string | null; created_at: string;
};
export type NightRun = {
  id: string; kind: string; status: string; trigger: string; started_at: string; finished_at: string | null;
  summary: Record<string, unknown>; error: string | null;
};
export type NightStatus = {
  enabled: boolean; scheduler: boolean; scheduler_error: string | null; running: NightRun | null; last_run: NightRun | null;
  next: { night_window: [string, string]; in_window: boolean; next_briefing: string } | null; timezone: string;
  window: { start: string; end: string; interval_minutes: number; day_interval_minutes: number; briefing_time: string };
  policy: { auto_rollback: boolean; auto_redeploy: boolean; fix_prs: boolean; drafts: boolean; max_cost_usd: number; max_fixes: number };
  connectors: { vercel: string | null; github: string | null; notifier: string | null; prober: string }; demo: boolean;
};
export type SetupCheck = { name: string; status: "ok" | "missing" | "error" | "optional" | "warning"; detail: string; how: string };

export async function patch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, { method: "PATCH", headers: headers(), body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

/** POST that surfaces the API's error detail (e.g. "c'è già un giro in corso"). */
export async function act<T>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: headers(), body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { detail?: string }).detail || `${path}: ${r.status}`);
  return data as T;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "mai";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "adesso";
  if (s < 3600) return `${Math.round(s / 60)} min fa`;
  if (s < 86400) return `${Math.round(s / 3600)} h fa`;
  return new Date(iso).toLocaleDateString("it-IT", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function clock(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "—";
}
