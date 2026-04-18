import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';

interface RunDetail {
  run_id: string;
  session_id: string;
  status: string;
  model: string;
  provider: string;
  created_at: number;
  updated_at: number;
  usage?: Record<string, any>;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export function RunPanel() {
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('idle');
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [elapsed, setElapsed] = useState<number>(0);

  // Listen to run sync events from ChatPage
  useEffect(() => {
    const handleSync = (e: CustomEvent) => {
      const { runId: newRunId, status: newStatus } = e.detail;
      if (newRunId !== runId) {
        setRunId(newRunId);
        setElapsed(0);
      }
      setStatus(newStatus || 'idle');
    };
    window.addEventListener('hermes-run-sync', handleSync as EventListener);
    
    // Request an immediate sync
    window.dispatchEvent(new CustomEvent('hermes-run-request-sync'));

    return () => {
      window.removeEventListener('hermes-run-sync', handleSync as EventListener);
    };
  }, [runId]);

  // Fetch run details
  useEffect(() => {
    if (!runId) {
      setDetail(null);
      return;
    }
    const fetchDetail = async () => {
      try {
        const res = await apiClient.get<{ ok: boolean; run: RunDetail }>(`/chat/run/${runId}`);
        if (res.ok) {
          setDetail(res.run);
        }
      } catch (err) {
        console.error('Failed to fetch run details:', err);
      }
    };

    fetchDetail();
    // Poll detail if running
    if (status === 'running') {
      const interval = setInterval(fetchDetail, 2000);
      return () => clearInterval(interval);
    }
  }, [runId, status]);

  // Elapsed timer
  useEffect(() => {
    if (status !== 'running') return;
    const interval = setInterval(() => {
      setElapsed((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(interval);
  }, [status]);

  if (!runId && status === 'idle') {
    return (
      <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>
        <div style={{ fontSize: '2rem', marginBottom: '16px' }}>😴</div>
        <p>No active run.</p>
        <p style={{ fontSize: '0.875rem' }}>Send a message to start a new run.</p>
      </div>
    );
  }

  const modelName = detail?.model || 'N/A';
  const provider = detail?.provider || 'N/A';
  
  // Format token counts
  const pTokens = detail?.prompt_tokens ?? detail?.usage?.prompt_tokens ?? 0;
  const cTokens = detail?.completion_tokens ?? detail?.usage?.completion_tokens ?? 0;
  const tTokens = detail?.total_tokens ?? detail?.usage?.total_tokens ?? (pTokens + cTokens);

  return (
    <div style={{ padding: '16px', color: '#f8fafc' }}>
      <h3 style={{ marginTop: 0, marginBottom: '12px', fontSize: '1.1rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
        {status === 'running' ? '⏳ Running...' : status === 'failed' ? '❌ Failed' : '✅ Completed'}
      </h3>
      
      <div style={{ background: 'rgba(0,0,0,0.2)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)', marginBottom: '16px' }}>
        <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Run ID</div>
        <div style={{ fontFamily: 'monospace', fontSize: '0.9rem' }}>{runId || 'N/A'}</div>
      </div>

      <div style={{ background: 'rgba(0,0,0,0.2)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)', marginBottom: '16px' }}>
        <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Model & Provider</div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontWeight: 500 }}>{modelName}</span>
          <span style={{ fontSize: '0.8rem', background: 'rgba(99, 102, 241, 0.2)', color: '#818cf8', padding: '2px 6px', borderRadius: '4px' }}>
            {provider}
          </span>
        </div>
      </div>

      {status === 'running' && (
        <div style={{ background: 'rgba(0,0,0,0.2)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)', marginBottom: '16px' }}>
          <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Elapsed Time</div>
          <div style={{ fontSize: '1.25rem', fontFamily: 'monospace' }}>
            {Math.floor(elapsed / 60)}:{(elapsed % 60).toString().padStart(2, '0')}
          </div>
        </div>
      )}

      {(status === 'completed' || tTokens > 0) && (
        <div style={{ background: 'rgba(0,0,0,0.2)', padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Run Token Usage</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '0.9rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#cbd5e1' }}>Prompt:</span>
              <span style={{ fontFamily: 'monospace' }}>{pTokens.toLocaleString()}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#cbd5e1' }}>Completion:</span>
              <span style={{ fontFamily: 'monospace' }}>{cTokens.toLocaleString()}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '8px', fontWeight: 600 }}>
              <span>Total:</span>
              <span style={{ fontFamily: 'monospace' }}>{tTokens.toLocaleString()}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
