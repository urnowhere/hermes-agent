import { useEffect, useState } from 'react';
import { apiClient } from '../lib/api';
import { PlatformCards } from '../components/gateway/PlatformCards';
import { PlatformConfigModal } from '../components/gateway/PlatformConfigModal';
import { toastStore } from '../store/toastStore';

interface GatewayPlatform {
  key: string;
  label: string;
  enabled?: boolean;
  configured?: boolean;
  runtime_state?: string;
  error_code?: string | null;
  error_message?: string | null;
}

interface GatewayPlatformsResponse {
  ok: boolean;
  platforms: GatewayPlatform[];
}

interface PairingEntry {
  platform: string;
  user_id?: string;
  code?: string;
  status?: string;
}

interface PairingResponse {
  ok: boolean;
  pairing?: {
    pending?: PairingEntry[];
    approved?: PairingEntry[];
  };
}

interface OverviewResponse {
  ok: boolean;
  overview?: {
    gateway?: { running?: boolean; pid?: number; state?: string; exit_reason?: string | null; updated_at?: string };
    summary?: {
      platform_count?: number;
      enabled_platforms?: number;
      configured_platforms?: number;
      connected_platforms?: number;
      pending_pairings?: number;
      approved_pairings?: number;
    };
  };
}

export function GatewayPage() {
  const [platforms, setPlatforms] = useState<Array<{ id: string; name: string; status: string; detail: string }>>([]);
  const [pendingPairings, setPendingPairings] = useState<PairingEntry[]>([]);
  const [approvedPairings, setApprovedPairings] = useState<PairingEntry[]>([]);
  const [overview, setOverview] = useState<{ total: number; connected: number; enabled: number } | null>(null);

  const [configuringPlatform, setConfiguringPlatform] = useState<string | null>(null);

  const refreshAll = async () => {
    const [platRes, pairingRes, overviewRes] = await Promise.all([
      apiClient.get<GatewayPlatformsResponse>('/gateway/platforms').catch(() => null),
      apiClient.get<PairingResponse>('/gateway/pairing').catch(() => null),
      apiClient.get<OverviewResponse>('/gateway/overview').catch(() => null),
    ]);

    if (overviewRes?.ok && overviewRes.overview) {
      const summary = overviewRes.overview.summary;
      setOverview({
        total: summary?.platform_count ?? 0,
        connected: summary?.connected_platforms ?? 0,
        enabled: summary?.enabled_platforms ?? 0,
      });
    }

    if (platRes?.ok && platRes.platforms) {
      setPlatforms(
        platRes.platforms.map((p) => ({
          id: p.key,
          name: p.label,
          status: p.runtime_state === 'connected' ? 'connected' : (p.enabled ? 'enabled' : 'disabled'),
          detail: p.error_message ?? (p.configured ? 'configured' : 'not configured'),
        }))
      );
    }

    if (pairingRes?.ok && pairingRes.pairing) {
      setPendingPairings(pairingRes.pairing.pending ?? []);
      setApprovedPairings(pairingRes.pairing.approved ?? []);
    }
  };

  useEffect(() => { refreshAll(); }, []);

  const handleApprovePairing = async (platform: string, code: string) => {
    await apiClient.post('/gateway/pairing/approve', { platform, code });
    await refreshAll();
  };

  const handleRevokePairing = async (platform: string, userId: string) => {
    await apiClient.post('/gateway/pairing/revoke', { platform, user_id: userId });
    await refreshAll();
  };

  const handleTogglePlatform = async (platformId: string, action: 'start' | 'stop') => {
    const res = await apiClient.post<{ok: boolean, reload_required?: boolean}>(`/gateway/platforms/${platformId}/${action}`);
    if (res?.ok) {
      toastStore.success(`Platform ${platformId} ${action === 'start' ? 'enabled' : 'disabled'}`);
      if (res.reload_required) {
         toastStore.info('Please restart the Gateway for changes to apply.');
      }
      await refreshAll();
    } else {
      toastStore.error(`Failed to ${action} ${platformId}`);
    }
  };

  const btnStyle: React.CSSProperties = {
    padding: '5px 12px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.8rem',
    border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.06)',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flex: 1, overflowY: 'auto', minHeight: 0, paddingBottom: '30px', paddingRight: '12px' }}>
      {/* Overview stats bar */}
      {overview && (
        <div style={{
          display: 'flex', gap: '16px', padding: '14px 18px', borderRadius: '14px',
          background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
        }}>
          {[{ label: 'Total', value: overview.total, color: '#a5b4fc' },
            { label: 'Enabled', value: overview.enabled, color: '#fde68a' },
            { label: 'Connected', value: overview.connected, color: '#86efac' }].map((s) => (
            <div key={s.label} style={{ display: 'flex', alignItems: 'baseline', gap: '6px' }}>
              <span style={{ fontSize: '1.5rem', fontWeight: 700, color: s.color }}>{s.value}</span>
              <span style={{ fontSize: '0.8rem', color: '#64748b' }}>{s.label}</span>
            </div>
          ))}
        </div>
      )}

      <PlatformCards 
        platforms={platforms} 
        onConfigure={(id: string) => setConfiguringPlatform(id)}
        onToggle={handleTogglePlatform}
      />

      {configuringPlatform && (
        <PlatformConfigModal 
          platformId={configuringPlatform} 
          onClose={() => setConfiguringPlatform(null)} 
          onSaved={() => {
            setConfiguringPlatform(null);
            refreshAll();
          }} 
        />
      )}

      {/* Pending Pairings */}
      {pendingPairings.length > 0 && (
        <section style={{
          background: 'rgba(251, 191, 36, 0.06)',
          border: '1px solid rgba(251, 191, 36, 0.2)',
          borderRadius: '16px', padding: '20px',
        }}>
          <h3 style={{ margin: '0 0 12px', color: '#fde68a', fontSize: '1rem' }}>⏳ Pending Pairing Requests</h3>
          {pendingPairings.map((p, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 12px', background: 'rgba(0,0,0,0.1)', borderRadius: '10px', marginBottom: '6px' }}>
              <div>
                <strong style={{ color: '#e2e8f0', textTransform: 'capitalize' }}>{p.platform}</strong>
                <code style={{ color: '#a5b4fc', marginLeft: '8px', fontSize: '0.85rem' }}>{p.code}</code>
              </div>
              <button type="button" onClick={() => handleApprovePairing(p.platform, p.code ?? '')} style={{ ...btnStyle, color: '#86efac', borderColor: 'rgba(34,197,94,0.3)' }}>
                ✓ Approve
              </button>
            </div>
          ))}
        </section>
      )}

      {/* Approved Pairings */}
      {approvedPairings.length > 0 && (
        <section style={{
          background: 'rgba(255, 255, 255, 0.03)',
          border: '1px solid rgba(255, 255, 255, 0.06)',
          borderRadius: '16px', padding: '20px',
        }}>
          <h3 style={{ margin: '0 0 12px', color: '#e2e8f0', fontSize: '1rem' }}>🔗 Approved Pairings</h3>
          {approvedPairings.map((p, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 12px', background: 'rgba(0,0,0,0.1)', borderRadius: '10px', marginBottom: '6px' }}>
              <div>
                <strong style={{ color: '#e2e8f0', textTransform: 'capitalize' }}>{p.platform}</strong>
                <span style={{ color: '#64748b', marginLeft: '8px', fontSize: '0.85rem' }}>{p.user_id}</span>
              </div>
              <button type="button" onClick={() => handleRevokePairing(p.platform, p.user_id ?? '')} style={{ ...btnStyle, color: '#fca5a5', borderColor: 'rgba(239,68,68,0.3)' }}>
                ✕ Revoke
              </button>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
