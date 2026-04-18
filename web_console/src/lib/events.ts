import { getBackendUrl } from '../store/backendStore';

export interface EventSubscription {
  close(): void;
}

export interface GuiEvent {
  type: string;
  session_id: string;
  run_id: string | null;
  payload: Record<string, unknown>;
  ts: number;
}

export type EventStreamCallback = (event: GuiEvent) => void;

export function openSessionEventStream(sessionId: string, onMessage: EventStreamCallback): EventSubscription {
  const base = getBackendUrl();
  const url = `${base}/api/gui/stream/session/${encodeURIComponent(sessionId)}`;
  const source = new EventSource(url);

  source.onmessage = (raw) => {
    try {
      const event: GuiEvent = JSON.parse(raw.data);
      onMessage(event);
    } catch {
      // ignore malformed frames
    }
  };

  source.onerror = () => {
    // Let the browser handle reconnection automatically.
  };

  return {
    close() {
      source.close();
    }
  };
}
