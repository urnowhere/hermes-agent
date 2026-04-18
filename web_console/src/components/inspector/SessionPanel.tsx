import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';

interface SessionDetail {
  session_id: string;
  title: string | null;
  started_at: number;
  ended_at: number | null;
  end_reason: string | null;
  model: string;
  system_prompt: string;
  source: string | null;
  message_count: number;
  token_summary: {
    input: number;
    output: number;
    reasoning: number;
    cache_read: number;
    cache_write: number;
    total: number;
  };
  metadata: {
    model_config?: Record<string, any>;
    estimated_cost_usd?: number;
    billing_provider?: string;
  };
}

export function SessionPanel() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const handleSync = (e: any) => {
      if (e.detail?.sessionId && e.detail.sessionId !== 'current') {
        setSessionId(e.detail.sessionId);
      }
    };
    window.addEventListener('hermes-session-sync', handleSync);
    window.dispatchEvent(new CustomEvent('hermes-run-request-sync'));
    return () => window.removeEventListener('hermes-session-sync', handleSync);
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    setLoading(true);
    apiClient.get<{ ok: boolean; session: SessionDetail }>(`/sessions/${sessionId}`)
      .then(res => {
        if (active && res.ok) {
          setSession(res.session);
        }
      })
      .catch(err => console.error('Failed to load session:', err))
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [sessionId]);

  if (!sessionId) {
    return <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>No active session.</div>;
  }

  if (loading) {
    return <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>Loading session...</div>;
  }

  if (!session) {
    return <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>Session not found.</div>;
  }

  const elapsed = session.ended_at 
    ? Math.round(session.ended_at - session.started_at)
    : Math.round(Date.now() / 1000 - session.started_at);

  return (
    <div style={{ padding: '16px', color: '#f8fafc', display: 'flex', flexDirection: 'column', gap: '20px', overflowY: 'auto', height: '100%' }}>
      {/* Header */}
      <div>
        <h3 style={{ margin: '0 0 8px 0', fontSize: '1rem', color: '#e2e8f0' }}>{session.title || 'Untitled Session'}</h3>
        <div style={{ fontSize: '0.75rem', color: '#64748b', fontFamily: 'monospace' }}>{session.session_id}</div>
      </div>

      {/* Info Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: '12px', borderRadius: '6px' }}>
          <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: '#818cf8', marginBottom: '4px' }}>Model</div>
          <div style={{ fontSize: '0.875rem' }}>{session.model.split('/').pop()}</div>
        </div>
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: '12px', borderRadius: '6px' }}>
          <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: '#818cf8', marginBottom: '4px' }}>Source</div>
          <div style={{ fontSize: '0.875rem' }}>{session.source || 'web_console'}</div>
        </div>
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: '12px', borderRadius: '6px' }}>
          <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: '#818cf8', marginBottom: '4px' }}>Duration</div>
          <div style={{ fontSize: '0.875rem' }}>
            {elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`}
            {session.ended_at ? ' (ended)' : ' (active)'}
          </div>
        </div>
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: '12px', borderRadius: '6px' }}>
          <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', color: '#818cf8', marginBottom: '4px' }}>Turns</div>
          <div style={{ fontSize: '0.875rem' }}>{session.message_count} messages</div>
        </div>
      </div>

      {/* Token Usage */}
      <div>
        <h4 style={{ margin: '0 0 12px 0', fontSize: '0.8rem', textTransform: 'uppercase', color: '#818cf8' }}>Token Stats</h4>
        <div style={{ background: 'rgba(255,255,255,0.03)', padding: '12px', borderRadius: '6px', fontSize: '0.875rem', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: '#94a3b8' }}>Total</span>
            <span style={{ fontWeight: 600 }}>{(session.token_summary.total).toLocaleString()}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: '#94a3b8' }}>Input</span>
            <span>{(session.token_summary.input).toLocaleString()}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: '#94a3b8' }}>Output</span>
            <span>{(session.token_summary.output).toLocaleString()}</span>
          </div>
          {session.token_summary.reasoning > 0 && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#94a3b8' }}>Reasoning</span>
              <span style={{ color: '#eab308' }}>{(session.token_summary.reasoning).toLocaleString()}</span>
            </div>
          )}
          {session.token_summary.cache_read > 0 && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#94a3b8' }}>Cache Read</span>
              <span style={{ color: '#22c55e' }}>{(session.token_summary.cache_read).toLocaleString()}</span>
            </div>
          )}
        </div>
      </div>

      {/* Pricing */}
      {session.metadata?.estimated_cost_usd && (
        <div style={{ display: 'flex', justifyContent: 'space-between', background: 'rgba(255,255,255,0.03)', padding: '12px', borderRadius: '6px', fontSize: '0.875rem' }}>
          <span style={{ color: '#94a3b8' }}>Est. Cost</span>
          <span style={{ color: '#4ade80' }}>${session.metadata.estimated_cost_usd.toFixed(4)}</span>
        </div>
      )}

      {/* System Prompt View */}
      {session.system_prompt && (
        <div>
          <h4 style={{ margin: '0 0 12px 0', fontSize: '0.8rem', textTransform: 'uppercase', color: '#818cf8', display: 'flex', justifyContent: 'space-between' }}>
            <span>System Prompt</span>
            <span style={{ fontSize: '0.7rem', color: '#64748b' }}>{session.system_prompt.length.toLocaleString()} chars</span>
          </h4>
          <div style={{ 
            background: 'var(--vscode-editor-background, #1e1e1e)', 
            padding: '12px', 
            borderRadius: '6px', 
            fontSize: '0.75rem',
            fontFamily: 'monospace',
            maxHeight: '200px',
            overflowY: 'auto',
            color: '#d4d4d4',
            whiteSpace: 'pre-wrap'
          }}>
            {session.system_prompt}
          </div>
        </div>
      )}
    </div>
  );
}
