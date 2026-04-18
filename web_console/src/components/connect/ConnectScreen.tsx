import { useState, useEffect, type ReactNode } from 'react';
import { isLocalMode, getBackendUrl, setBackendUrl, hasSavedBackendUrl } from '../../store/backendStore';
import { useConnection } from '../../lib/connectionContext';

/**
 * ConnectGate — wraps the main app and shows a connect screen
 * when running in hosted mode without a live backend connection.
 *
 * In local mode (dev/self-hosted), it always renders children directly.
 */
export function ConnectGate({ children }: { children: ReactNode }) {
  const connection = useConnection();

  // In local mode, skip the gate entirely — backwards compatible
  if (isLocalMode()) {
    return <>{children}</>;
  }

  // In hosted mode: show children if connected, connect screen if not
  if (connection.online) {
    return <>{children}</>;
  }

  return <ConnectScreen />;
}

function ConnectScreen() {
  const connection = useConnection();
  const [url, setUrl] = useState(() => getBackendUrl() || 'http://localhost:8642');
  const [status, setStatus] = useState<'idle' | 'testing' | 'error' | 'success'>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  // Auto-connect on mount if a saved URL exists
  useEffect(() => {
    if (hasSavedBackendUrl()) {
      handleConnect();
    }
  }, []);

  const handleConnect = async () => {
    setStatus('testing');
    setErrorMsg('');

    const testUrl = url.replace(/\/+$/, '');

    try {
      const res = await fetch(`${testUrl}/api/gui/metrics/global`, {
        signal: AbortSignal.timeout(5000),
      });

      if (!res.ok) throw new Error(`Server responded with ${res.status}`);
      const data = await res.json();

      if (data.ok) {
        setStatus('success');
        setBackendUrl(testUrl);
        // Trigger reconnect so the ConnectionProvider picks up the new URL
        setTimeout(() => connection.reconnect(), 300);
      } else {
        throw new Error('Server responded but health check failed');
      }
    } catch (err) {
      setStatus('error');
      if (err instanceof TypeError && err.message.includes('fetch')) {
        setErrorMsg('Cannot reach server. Is Hermes running? Check that your gateway is started.');
      } else if (err instanceof DOMException && err.name === 'TimeoutError') {
        setErrorMsg('Connection timed out. Make sure your Hermes backend is running on the specified port.');
      } else {
        setErrorMsg(err instanceof Error ? err.message : 'Connection failed');
      }
    }
  };

  return (
    <div style={{
      height: '100vh',
      width: '100vw',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%)',
      fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif",
    }}>
      {/* Ambient glow */}
      <div style={{
        position: 'absolute',
        width: '600px',
        height: '600px',
        borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(99, 102, 241, 0.08) 0%, transparent 70%)',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        pointerEvents: 'none',
      }} />

      <div style={{
        position: 'relative',
        width: '100%',
        maxWidth: '460px',
        padding: '48px 40px',
        background: 'rgba(15, 23, 42, 0.85)',
        backdropFilter: 'blur(24px)',
        WebkitBackdropFilter: 'blur(24px)',
        border: '1px solid rgba(129, 140, 248, 0.15)',
        borderRadius: '24px',
        boxShadow: '0 25px 50px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.05)',
      }}>
        {/* Logo */}
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <div style={{
            fontSize: '3rem',
            marginBottom: '12px',
            filter: 'drop-shadow(0 0 20px rgba(99, 102, 241, 0.3))',
          }}>⚡</div>
          <h1 style={{
            margin: 0,
            fontSize: '1.75rem',
            fontWeight: 700,
            background: 'linear-gradient(135deg, #e2e8f0, #a5b4fc)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            letterSpacing: '-0.02em',
          }}>Hermes Console</h1>
          <p style={{
            margin: '8px 0 0',
            fontSize: '0.9rem',
            color: '#64748b',
            lineHeight: 1.5,
          }}>
            Connect to your local Hermes agent
          </p>
        </div>

        {/* Connection form */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <label style={{
              display: 'block',
              fontSize: '0.8rem',
              fontWeight: 500,
              color: '#94a3b8',
              marginBottom: '6px',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}>Backend URL</label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleConnect(); }}
              placeholder="http://localhost:8642"
              style={{
                width: '100%',
                padding: '12px 16px',
                borderRadius: '12px',
                border: `1px solid ${status === 'error' ? 'rgba(239, 68, 68, 0.4)' : 'rgba(255, 255, 255, 0.1)'}`,
                background: 'rgba(0, 0, 0, 0.3)',
                color: '#e2e8f0',
                fontSize: '0.95rem',
                fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                outline: 'none',
                transition: 'border-color 0.2s, box-shadow 0.2s',
                boxSizing: 'border-box',
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = 'rgba(129, 140, 248, 0.5)';
                e.currentTarget.style.boxShadow = '0 0 0 3px rgba(129, 140, 248, 0.1)';
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.1)';
                e.currentTarget.style.boxShadow = 'none';
              }}
            />
          </div>

          <button
            onClick={handleConnect}
            disabled={status === 'testing'}
            style={{
              padding: '14px 24px',
              borderRadius: '12px',
              border: 'none',
              background: status === 'testing'
                ? 'rgba(99, 102, 241, 0.3)'
                : 'linear-gradient(135deg, #6366f1, #818cf8)',
              color: '#fff',
              fontWeight: 600,
              fontSize: '1rem',
              cursor: status === 'testing' ? 'wait' : 'pointer',
              transition: 'all 0.2s',
              boxShadow: status === 'testing' ? 'none' : '0 4px 14px rgba(99, 102, 241, 0.3)',
              letterSpacing: '0.01em',
            }}
            onMouseEnter={(e) => {
              if (status !== 'testing') {
                e.currentTarget.style.transform = 'translateY(-1px)';
                e.currentTarget.style.boxShadow = '0 6px 20px rgba(99, 102, 241, 0.4)';
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = 'none';
              e.currentTarget.style.boxShadow = '0 4px 14px rgba(99, 102, 241, 0.3)';
            }}
          >
            {status === 'testing' ? '⏳ Connecting…' : '⚡ Connect'}
          </button>

          {/* Status messages */}
          {status === 'error' && (
            <div style={{
              padding: '12px 16px',
              borderRadius: '10px',
              background: 'rgba(239, 68, 68, 0.08)',
              border: '1px solid rgba(239, 68, 68, 0.2)',
              color: '#fca5a5',
              fontSize: '0.85rem',
              lineHeight: 1.5,
            }}>
              <strong>Connection failed.</strong> {errorMsg}
            </div>
          )}

          {status === 'success' && (
            <div style={{
              padding: '12px 16px',
              borderRadius: '10px',
              background: 'rgba(74, 222, 128, 0.08)',
              border: '1px solid rgba(74, 222, 128, 0.2)',
              color: '#4ade80',
              fontSize: '0.85rem',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}>
              <span>✓</span> Connected! Loading console…
            </div>
          )}
        </div>

        {/* Help text */}
        <div style={{
          marginTop: '28px',
          paddingTop: '20px',
          borderTop: '1px solid rgba(255, 255, 255, 0.06)',
          textAlign: 'center',
        }}>
          <p style={{ margin: 0, fontSize: '0.8rem', color: '#475569', lineHeight: 1.6 }}>
            Make sure Hermes is running locally with the API server enabled.
          </p>
          <pre style={{
            margin: '12px auto 0',
            padding: '10px 16px',
            borderRadius: '8px',
            background: 'rgba(0, 0, 0, 0.3)',
            border: '1px solid rgba(255, 255, 255, 0.06)',
            color: '#94a3b8',
            fontSize: '0.8rem',
            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            display: 'inline-block',
            textAlign: 'left',
          }}>hermes --gui</pre>
        </div>
      </div>
    </div>
  );
}
