import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { getBackendUrl, isLocalMode } from '../store/backendStore';

export type BackendMode = 'hermes' | 'generic' | 'offline';

interface ConnectionState {
  mode: BackendMode;
  online: boolean;
  lastCheck: number;
  /** Trigger a reconnect attempt (used by ConnectScreen after URL change). */
  reconnect: () => void;
}

const ConnectionContext = createContext<ConnectionState>({
  mode: 'hermes',
  online: true,
  lastCheck: Date.now(),
  reconnect: () => {},
});

export function useConnection(): ConnectionState {
  return useContext(ConnectionContext);
}

/** Routes that require a live Hermes backend */
export const HERMES_ONLY_ROUTES = new Set(['skills', 'memory', 'jobs']);

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<ConnectionState, 'reconnect'>>({
    mode: 'hermes',
    online: true,
    lastCheck: Date.now(),
  });

  const [tick, setTick] = useState(0);

  const healthCheck = useCallback(async () => {
    try {
      const base = getBackendUrl();
      const res = await fetch(`${base}/api/gui/metrics/global`, { signal: AbortSignal.timeout(4000) });
      if (!res.ok) throw new Error('not ok');
      const data = await res.json();
      setState({
        mode: data.ok ? 'hermes' : 'generic',
        online: true,
        lastCheck: Date.now(),
      });
    } catch {
      setState(prev => ({
        ...prev,
        mode: 'offline',
        online: false,
        lastCheck: Date.now(),
      }));
    }
  }, [tick]);

  const reconnect = useCallback(() => {
    // Bump tick to force healthCheck to re-create with fresh URL
    setTick(t => t + 1);
  }, []);

  useEffect(() => {
    healthCheck();
    const interval = setInterval(healthCheck, 10000);
    return () => clearInterval(interval);
  }, [healthCheck]);

  return (
    <ConnectionContext.Provider value={{ ...state, reconnect }}>
      {children}
    </ConnectionContext.Provider>
  );
}
