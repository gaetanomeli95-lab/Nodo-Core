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
