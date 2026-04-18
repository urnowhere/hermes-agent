import { useState, useEffect } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

interface PoolEntry {
  id: string;
  label: string;
  auth_type: string;
  source: string;
  priority: number;
  last_refresh?: string;
  request_count: number;
  is_active?: boolean;
}

interface PoolResponse {
  ok: boolean;
  entries: PoolEntry[];
}

interface CodexAuthModalProps {
  onClose: () => void;
}

export function CodexAuthModal({ onClose }: CodexAuthModalProps) {
  const [entries, setEntries] = useState<PoolEntry[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Device Auth State
  const [authStep, setAuthStep] = useState<'idle' | 'polling' | 'complete'>('idle');
  const [deviceCode, setDeviceCode] = useState('');
  const [userCode, setUserCode] = useState('');
  const [verificationUri, setVerificationUri] = useState('');
  const [expiresIn, setExpiresIn] = useState(0);

  const fetchPool = async () => {
    try {
      setLoading(true);
      const res = await apiClient.get<PoolResponse>('/credentials/pool?provider=openai-codex');
      if (res.ok) {
        setEntries(res.entries || []);
      }
    } catch (err) {
      toastStore.error('Failed to load Codex accounts');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPool();
  }, []);

  const handleDelete = async (id: string, label: string) => {
    if (!window.confirm(`Remove credential "${label}"?`)) return;
    try {
      const res = await apiClient.del<any>(`/credentials/pool/${id}?provider=openai-codex`);
      if (res.ok) {
        toastStore.success('Credential removed');
        fetchPool();
      } else {
        toastStore.error(res.error || 'Failed to remove credential');
      }
    } catch (err) {
      toastStore.error('Failed to remove credential');
    }
  };

  const handleSetActive = async (id: string) => {
    try {
      const res = await apiClient.post<any>(`/credentials/pool/${id}/activate?provider=openai-codex`);
      if (res.ok) {
        toastStore.success('Account activated');
        fetchPool();
      } else {
        toastStore.error(res.error || 'Failed to activate account');
      }
    } catch (err) {
      toastStore.error('Failed to activate account');
    }
  };

  const startAuth = async () => {
    try {
      setAuthStep('polling');
      const res = await apiClient.post<any>('/credentials/device-auth', { provider: 'openai-codex' });
      if (res.ok) {
        setDeviceCode(res.device_code);
        setUserCode(res.user_code);
        setVerificationUri(res.verification_uri_complete || res.verification_uri);
        setExpiresIn(res.expires_in);
        pollAuth(res.device_code);
      } else {
        toastStore.error(res.error || 'Failed to start auth');
        setAuthStep('idle');
      }
    } catch (err) {
      toastStore.error('Failed to start auth');
      setAuthStep('idle');
    }
  };

  const pollAuth = async (code: string) => {
    let polling = true;
    while (polling) {
      await new Promise(r => setTimeout(r, 5000));
      try {
        const res = await apiClient.post<any>('/credentials/device-auth/poll', { device_code: code });
        if (!res.ok || res.status === 'error' || res.status === 'expired') {
          toastStore.error(res.error || `Auth ${res.status || 'failed'}`);
          setAuthStep('idle');
          polling = false;
        } else if (res.status === 'complete') {
          toastStore.success(`Successfully authenticated as ${res.label}`);
          setAuthStep('complete');
          fetchPool();
          polling = false;
          setTimeout(() => setAuthStep('idle'), 3000);
        }
        // if pending, continue mapping
      } catch {
        // network issue, keep trying
      }
    }
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
      background: 'rgba(0, 0, 0, 0.6)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100
    }}>
      <div style={{
        background: '#1e293b', border: '1px solid rgba(255, 255, 255, 0.1)',
        borderRadius: '16px', padding: '24px', width: '500px', maxWidth: '90vw',
        maxHeight: '90vh', display: 'flex', flexDirection: 'column', gap: '20px'
      }}>
        <h3 style={{ margin: 0, color: '#e2e8f0' }}>OpenAI Codex Accounts</h3>
        
        {loading ? (
          <div style={{ color: '#94a3b8' }}>Loading accounts...</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', overflowY: 'auto' }}>
            {entries.length === 0 ? (
              <div style={{ padding: '16px', background: 'rgba(0,0,0,0.2)', borderRadius: '8px', color: '#94a3b8', textAlign: 'center' }}>
                No Codex accounts configured.
              </div>
            ) : (
              entries.map(e => (
                <div key={e.id} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '12px 16px', background: 'rgba(255,255,255,0.05)',
                  borderRadius: '10px', border: '1px solid rgba(255,255,255,0.05)'
                }}>
                  <div>
                    <div style={{ fontWeight: 600, color: '#e2e8f0', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {e.label || 'Unknown Account'}
                      {e.is_active && (
                        <span style={{
                          background: 'rgba(34, 197, 94, 0.2)', color: '#4ade80',
                          padding: '2px 6px', borderRadius: '4px', fontSize: '0.65rem', fontWeight: 'bold'
                        }}>✓ Active</span>
                      )}
                    </div>
                    <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '4px' }}>
                      ID: {e.id} • Requests: {e.request_count}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {!e.is_active && (
                      <button onClick={() => handleSetActive(e.id)} style={{
                        background: 'rgba(56, 189, 248, 0.1)', border: '1px solid rgba(56, 189, 248, 0.2)',
                        color: '#38bdf8', borderRadius: '6px', cursor: 'pointer', fontSize: '0.8rem', padding: '4px 8px'
                      }}>Set Active</button>
                    )}
                    <button onClick={() => handleDelete(e.id, e.label)} style={{
                      background: 'none', border: 'none', color: '#ef4444',
                      cursor: 'pointer', fontSize: '0.8rem', padding: '4px'
                    }}>Remove</button>
                  </div>
                </div>
              ))
            )}
            
            {authStep === 'idle' && (
              <button onClick={startAuth} style={{
                marginTop: '10px', padding: '10px', background: 'rgba(56, 189, 248, 0.1)',
                border: '1px dashed rgba(56, 189, 248, 0.4)', borderRadius: '10px', color: '#38bdf8',
                cursor: 'pointer', fontWeight: 600, transition: 'all 0.2s'
              }}>
                + Add Another Account
              </button>
            )}
            
            {authStep === 'polling' && (
              <div style={{
                marginTop: '10px', padding: '16px', background: 'rgba(168, 85, 247, 0.1)',
                border: '1px solid rgba(168, 85, 247, 0.3)', borderRadius: '10px', color: '#e2e8f0',
                display: 'flex', flexDirection: 'column', gap: '12px'
              }}>
                <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#c084fc' }}>Waiting for Authorization...</div>
                <div style={{ fontSize: '0.85rem' }}>
                  1. Open this URL: <a href={verificationUri} target="_blank" rel="noreferrer" style={{ color: '#38bdf8' }}>{verificationUri}</a>
                </div>
                <div style={{ fontSize: '0.85rem' }}>
                  2. Enter code: <strong style={{ background: 'rgba(0,0,0,0.3)', padding: '2px 6px', borderRadius: '4px', letterSpacing: '1px' }}>{userCode}</strong>
                </div>
                <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
                  Expires in {Math.floor(expiresIn / 60)} minutes
                </div>
              </div>
            )}
            
            {authStep === 'complete' && (
              <div style={{
                marginTop: '10px', padding: '12px', background: 'rgba(34, 197, 94, 0.1)',
                border: '1px solid rgba(34, 197, 94, 0.3)', borderRadius: '10px', color: '#4ade80',
                textAlign: 'center', fontSize: '0.9rem', fontWeight: 600
              }}>
                ✓ Authentication Successful
              </div>
            )}
          </div>
        )}
        
        <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: '16px', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
          <button onClick={onClose} style={{
            padding: '8px 20px', borderRadius: '8px', background: 'rgba(255,255,255,0.1)',
            border: 'none', color: '#e2e8f0', cursor: 'pointer', fontWeight: 600
          }}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
