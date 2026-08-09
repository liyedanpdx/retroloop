/**
 * The one WebSocket a retro board opens, and everything that keeps it honest (#16).
 *
 * The access token never leaves this file. It is read from `src/api/client.ts`
 * and put straight into the URL #12 defined; the URL itself is never logged,
 * never stored and never handed to a component, because a query string
 * containing a JWT in a console line is the same leak as putting it in
 * `localStorage`.
 *
 * Three rules, all of them from `_docs/decisions.md` and #12:
 *
 * *No replay.* A reconnect does a full authoritative reload rather than asking
 * what it missed. Events that arrive during that reload are buffered and
 * applied afterwards, so a snapshot taken at T cannot overwrite an event from
 * T+1.
 *
 * *One socket, one timer.* Backoff is 1s, 2s, 4s, 8s, then 10s. A second socket
 * would double every event and make the "applied once" rule unprovable.
 *
 * *1008 means the token, once.* A policy close asks #13's shared silent refresh
 * for a new access token and retries exactly once. If that fails it stops and
 * lets #13's session handling take over rather than reconnecting forever.
 */
import { getAccessToken, refreshAccessToken } from "../api/client";

export type RetroEvent = { event: string; data: Record<string, unknown> };
export type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "offline";

export const BACKOFF_MS = [1000, 2000, 4000, 8000, 10000];
const POLICY_VIOLATION = 1008;

export type SocketHandlers = {
  onEvent: (event: RetroEvent) => void;
  /** Fired when a connection is (re)established, so the caller can reload. */
  onConnected: (isReconnect: boolean) => void;
  onStatus: (status: ConnectionStatus) => void;
};

export type SocketFactory = (url: string) => WebSocket;

const defaultFactory: SocketFactory = (url) => new WebSocket(url);

export function retroSocketUrl(retroId: string, token: string): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/ws/retro/${retroId}?token=${encodeURIComponent(token)}`;
}

export class RetroSocket {
  private socket: WebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private refreshedOnce = false;
  private opened = false;
  private closedByUs = false;

  constructor(
    private readonly retroId: string,
    private readonly handlers: SocketHandlers,
    private readonly factory: SocketFactory = defaultFactory
  ) {}

  start(): void {
    void this.open(false);
  }

  /** Route change or unmount: close, cancel retries, and stay closed. */
  stop(): void {
    this.closedByUs = true;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.socket !== null) {
      this.socket.onmessage = null;
      this.socket.onclose = null;
      this.socket.onopen = null;
      this.socket.close();
      this.socket = null;
    }
  }

  private async open(isReconnect: boolean): Promise<void> {
    if (this.closedByUs) {
      return;
    }
    const token = getAccessToken();
    if (!token) {
      this.handlers.onStatus("offline");
      return;
    }

    this.handlers.onStatus(isReconnect ? "reconnecting" : "connecting");
    const socket = this.factory(retroSocketUrl(this.retroId, token));
    this.socket = socket;

    socket.onopen = () => {
      this.opened = true;
      this.attempt = 0;
      this.refreshedOnce = false;
      // Status stays below "connected" until the caller's reload finishes: a
      // board that says Connected while showing stale data is lying.
      this.handlers.onConnected(isReconnect);
    };

    socket.onmessage = (message) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(String((message as MessageEvent).data));
      } catch {
        return; // Malformed frames are ignored, never rendered, never logged.
      }
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        typeof (parsed as RetroEvent).event === "string"
      ) {
        this.handlers.onEvent(parsed as RetroEvent);
      }
    };

    socket.onclose = (closeEvent) => {
      if (this.closedByUs) {
        return;
      }
      this.socket = null;
      const wasOpen = this.opened;
      this.opened = false;

      if ((closeEvent as CloseEvent).code === POLICY_VIOLATION && !this.refreshedOnce) {
        // The token, not the network. Ask for a new one, once.
        this.refreshedOnce = true;
        this.handlers.onStatus("reconnecting");
        void refreshAccessToken().then((token) => {
          if (token) {
            void this.open(true);
          } else {
            this.handlers.onStatus("offline");
          }
        });
        return;
      }
      if ((closeEvent as CloseEvent).code === POLICY_VIOLATION) {
        this.handlers.onStatus("offline");
        return;
      }

      this.scheduleRetry(wasOpen);
    };
  }

  private scheduleRetry(wasOpen: boolean): void {
    const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
    this.attempt += 1;
    this.handlers.onStatus(wasOpen || this.attempt > 1 ? "reconnecting" : "offline");
    if (this.timer !== null) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.open(true);
    }, delay);
  }
}
