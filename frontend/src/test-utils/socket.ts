/**
 * A WebSocket that never leaves the process, for #16's tests.
 *
 * Installed over `globalThis.WebSocket`, so `RetroSocket` builds its URL and
 * opens its connection exactly as it does in a browser. Nothing here dials
 * anything; `sockets` records every instance so a test can prove there is only
 * ever one.
 */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  closedWith: number | null = null;

  constructor(public readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  open(): void {
    this.onopen?.();
  }

  emit(event: string, data: unknown): void {
    this.onmessage?.({ data: JSON.stringify({ event, data }) });
  }

  emitRaw(text: string): void {
    this.onmessage?.({ data: text });
  }

  /** The server going away, as opposed to `close()` which is us going away. */
  drop(code = 1006): void {
    this.onclose?.({ code });
  }

  close(): void {
    this.closedWith = 1000;
  }
}

export function installFakeWebSocket(): typeof FakeWebSocket {
  FakeWebSocket.instances = [];
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
  return FakeWebSocket;
}

export function lastSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  if (!socket) {
    throw new Error("no socket was opened");
  }
  return socket;
}
