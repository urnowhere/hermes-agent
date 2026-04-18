import { useEffect, useMemo, useState } from 'react';
import { getBackendUrl } from '../../store/backendStore';
import { toastStore } from '../../store/toastStore';

interface DetailedHealthResponse {
  status: string;
  platform: string;
  gateway_state: string | null;
  platforms: Record<string, {
    state?: string | null;
    updated_at?: string | null;
    error_message?: string | null;
  }>;
  active_agents: number;
  exit_reason: string | null;
  updated_at: string | null;
  pid: number | null;
}

export function SystemManager() {
  const [backingUp, setBackingUp] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState<{ restored?: number; total?: number; errors?: string[] } | null>(null);
  const [health, setHealth] = useState<DetailedHealthResponse | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadHealth = async () => {
      try {
        const base = getBackendUrl();
        const response = await fetch(`${base}/health/detailed`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json() as DetailedHealthResponse;
        if (!cancelled) {
          setHealth(data);
          setHealthError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setHealthError(err instanceof Error ? err.message : String(err));
        }
      }
    };

    void loadHealth();
    const timer = window.setInterval(() => {
      void loadHealth();
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const platformEntries = useMemo(
    () => Object.entries(health?.platforms || {}).sort(([a], [b]) => a.localeCompare(b)),
    [health]
  );

  const handleBackup = async () => {
    setBackingUp(true);
    try {
      const base = getBackendUrl();
      const res = await fetch(`${base}/api/gui/system/backup`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: 'Download failed' }));
        throw new Error(err.error || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const filename = res.headers.get('Content-Disposition')?.match(/filename="(.+)"/)?.[1] || 'hermes-backup.zip';
      const fileCount = res.headers.get('X-Backup-Files') || '?';

      // Trigger download
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);

      toastStore.success(`Backup Downloaded`, `${fileCount} files saved as ${filename}`);
    } catch (err) {
      toastStore.error('Backup Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setBackingUp(false);
    }
  };

  const handleRestore = async (file: File) => {
    setRestoring(true);
    setRestoreResult(null);
    try {
      const base = getBackendUrl();
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch(`${base}/api/gui/system/restore`, {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (!data.ok) {
        throw new Error(data.error || 'Restore failed');
      }
      setRestoreResult({ restored: data.restored, total: data.total, errors: data.errors });
      toastStore.success('Restore Complete', `${data.restored}/${data.total} files restored`);
    } catch (err) {
      toastStore.error('Restore Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setRestoring(false);
    }
  };

  const cardStyle: React.CSSProperties = {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: '12px',
    padding: '20px',
  };

  const btnBase: React.CSSProperties = {
    padding: '10px 20px',
    borderRadius: '8px',
    border: '1px solid rgba(255,255,255,0.12)',
    cursor: 'pointer',
    fontSize: '0.85rem',
    fontWeight: 500,
    transition: 'all 0.2s ease',
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
  };

  const formatTimestamp = (raw?: string | null) => {
    if (!raw) return 'Unknown';
    const parsed = new Date(raw);
    return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString();
  };

  const statusTone = (state?: string | null) => {
    if (state === 'connected' || state === 'running' || state === 'ok') {
      return { color: '#4ade80', bg: 'rgba(74,222,128,0.12)' };
    }
    if (state === 'error' || state === 'failed' || state === 'stopped') {
      return { color: '#f87171', bg: 'rgba(248,113,113,0.12)' };
    }
    return { color: '#fbbf24', bg: 'rgba(251,191,36,0.12)' };
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'flex-start', marginBottom: '14px' }}>
          <div>
            <h3 style={{ margin: '0 0 4px 0', fontSize: '1rem', color: '#e2e8f0' }}>
              🩺 Backend Health
            </h3>
            <p style={{ margin: 0, fontSize: '0.78rem', color: '#64748b', lineHeight: 1.5 }}>
              Live status from <code style={{ fontSize: '0.72rem' }}>/health/detailed</code> for GUI/backend parity checks.
            </p>
          </div>
          {health && (
            <div style={{
              padding: '6px 10px',
              borderRadius: '999px',
              background: statusTone(health.gateway_state).bg,
              color: statusTone(health.gateway_state).color,
              fontSize: '0.78rem',
              fontWeight: 600,
              textTransform: 'capitalize',
              whiteSpace: 'nowrap',
            }}>
              {health.gateway_state || health.status}
            </div>
          )}
        </div>

        {healthError ? (
          <div style={{ color: '#fda4af', fontSize: '0.82rem' }}>
            Failed to load detailed health: {healthError}
          </div>
        ) : health ? (
          <>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
              gap: '10px',
              marginBottom: '14px',
            }}>
              {[
                ['Service', health.platform || 'unknown'],
                ['PID', health.pid != null ? String(health.pid) : 'Unknown'],
                ['Active Agents', String(health.active_agents ?? 0)],
                ['Updated', formatTimestamp(health.updated_at)],
              ].map(([label, value]) => (
                <div key={label} style={{
                  padding: '10px 12px',
                  borderRadius: '10px',
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}>
                  <div style={{ fontSize: '0.7rem', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</div>
                  <div style={{ marginTop: '4px', fontSize: '0.84rem', color: '#e2e8f0', fontWeight: 600 }}>{value}</div>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {platformEntries.map(([name, info]) => {
                const tone = statusTone(info.state);
                return (
                  <div key={name} style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: '12px',
                    alignItems: 'center',
                    padding: '10px 12px',
                    borderRadius: '10px',
                    background: 'rgba(255,255,255,0.03)',
                    border: '1px solid rgba(255,255,255,0.06)',
                  }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ color: '#e2e8f0', fontWeight: 600, fontSize: '0.84rem' }}>{name}</div>
                      <div style={{ color: '#94a3b8', fontSize: '0.74rem', marginTop: '2px' }}>
                        Updated {formatTimestamp(info.updated_at)}
                      </div>
                      {info.error_message && (
                        <div style={{ color: '#fda4af', fontSize: '0.74rem', marginTop: '4px' }}>
                          {info.error_message}
                        </div>
                      )}
                    </div>
                    <div style={{
                      flexShrink: 0,
                      padding: '6px 10px',
                      borderRadius: '999px',
                      background: tone.bg,
                      color: tone.color,
                      fontSize: '0.75rem',
                      fontWeight: 600,
                      textTransform: 'capitalize',
                    }}>
                      {info.state || 'unknown'}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <div style={{ color: '#94a3b8', fontSize: '0.82rem' }}>Loading detailed health…</div>
        )}
      </div>

      <div style={cardStyle}>
        <h3 style={{ margin: '0 0 4px 0', fontSize: '1rem', color: '#e2e8f0' }}>
          💾 System Backup & Restore
        </h3>
        <p style={{ margin: '0 0 16px 0', fontSize: '0.78rem', color: '#64748b', lineHeight: 1.5 }}>
          Download a full backup of your Hermes state (config, sessions, memories, skills, API keys)
          or restore from a previous backup archive.
        </p>

        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
          <button
            onClick={handleBackup}
            disabled={backingUp}
            style={{
              ...btnBase,
              background: backingUp ? 'rgba(56,189,248,0.08)' : 'rgba(56,189,248,0.12)',
              color: '#7dd3fc',
              opacity: backingUp ? 0.6 : 1,
            }}
          >
            {backingUp ? (
              <>⏳ Creating backup…</>
            ) : (
              <>📦 Download Backup</>
            )}
          </button>

          <label
            style={{
              ...btnBase,
              background: restoring ? 'rgba(251,191,36,0.08)' : 'rgba(251,191,36,0.12)',
              color: '#fde68a',
              opacity: restoring ? 0.6 : 1,
              cursor: restoring ? 'wait' : 'pointer',
            }}
          >
            {restoring ? (
              <>⏳ Restoring…</>
            ) : (
              <>📥 Restore from Backup</>
            )}
            <input
              type="file"
              accept=".zip"
              disabled={restoring}
              style={{ display: 'none' }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleRestore(file);
                e.target.value = '';
              }}
            />
          </label>
        </div>

        {restoreResult && (
          <div style={{
            marginTop: '12px',
            padding: '10px 14px',
            borderRadius: '8px',
            background: restoreResult.errors && restoreResult.errors.length > 0
              ? 'rgba(251,191,36,0.08)'
              : 'rgba(74,222,128,0.08)',
            border: '1px solid rgba(255,255,255,0.06)',
            fontSize: '0.78rem',
            color: '#94a3b8',
            lineHeight: 1.6,
          }}>
            <div style={{ color: '#e2e8f0', fontWeight: 500, marginBottom: '4px' }}>
              ✓ Restore complete: {restoreResult.restored}/{restoreResult.total} files
            </div>
            {restoreResult.errors && restoreResult.errors.length > 0 && (
              <div style={{ color: '#fbbf24', marginTop: '4px' }}>
                ⚠ {restoreResult.errors.length} file(s) skipped:
                {restoreResult.errors.slice(0, 5).map((e, i) => (
                  <div key={i} style={{ paddingLeft: '12px', fontSize: '0.72rem' }}>{e}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
