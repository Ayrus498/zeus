import { WS } from "./api";

export type Handler = (ev: Record<string, any>) => void; // eslint-disable-line @typescript-eslint/no-explicit-any

/** Auto-reconnecting JSON websocket. */
export class Socket {
  private ws?: WebSocket;
  private closed = false;
  private queue: string[] = [];
  constructor(private path: string, private onEvent: Handler, private onState?: (open: boolean) => void) { this.connect(); }
  private connect() {
    if (this.closed) return;
    const ws = new WebSocket(WS + this.path);
    this.ws = ws;
    ws.onopen = () => { this.onState?.(true); this.queue.splice(0).forEach((m) => ws.send(m)); };
    ws.onmessage = (m) => { try { this.onEvent(JSON.parse(m.data)); } catch { /* ignore */ } };
    ws.onclose = () => { this.onState?.(false); if (!this.closed) setTimeout(() => this.connect(), 1500); };
  }
  send(o: unknown) {
    const s = JSON.stringify(o);
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(s); else this.queue.push(s);
  }
  close() { this.closed = true; this.ws?.close(); }
}
