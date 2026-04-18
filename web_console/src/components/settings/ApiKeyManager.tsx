import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

interface ProviderInfo {
  provider: string;
  name: string;
  auth_type: string | null;
  active: boolean;
  status: Record<string, unknown>;
}

export interface AuthSchemaItem {
  description: string;
  prompt: string;
  url: string | null;
  password: boolean;
  category: string;
  advanced?: boolean;
  value: string;
  configured: boolean;
}

export interface AuthSchemaResponse {
  ok: boolean;
  schema?: Record<string, AuthSchemaItem>;
}

interface AuthStatusResponse {
  ok: boolean;
  auth?: {
    active_provider: string | null;
    resolved_provider: string | null;
    logged_in: boolean;
    active_status: Record<string, unknown>;
    providers: ProviderInfo[];
  };
}

const CATEGORY_ORDER = ['provider', 'tool', 'messaging'];

const CATEGORY_LABELS: Record<string, string> = {
  provider: 'LLM Providers',
  tool: 'Tool API Keys',
  messaging: 'Messaging Platforms',
};

import { ApiKeyConfigModal } from './ApiKeyConfigModal';
import { CodexAuthModal } from './CodexAuthModal';

export function ApiKeyManager() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [configModalCategories, setConfigModalCategories] = useState<string[] | null>(null);
  const [showCodexModal, setShowCodexModal] = useState(false);

  const fetchAuth = async () => {
    try {
      const res = await apiClient.get<AuthStatusResponse>('/auth-status');
      if (res.ok && res.auth) {
        setProviders(res.auth.providers || []);
        setActiveProvider(res.auth.active_provider);
      }
    } catch (err) {
      toastStore.error('Auth Status Load Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAuth();
  }, []);

  if (loading) {
    return (
      <div style={{ padding: '24px', color: '#64748b', textAlign: 'center' }}>
        Loading API key status…
      </div>
    );
  }

  if (providers.length === 0) {
    return (
      <div style={{ padding: '24px', color: '#64748b', textAlign: 'center' }}>
        No provider information available.
      </div>
    );
  }

  // Group providers by rough category based on known names
  const providerGroups = new Map<string, ProviderInfo[]>();
  for (const p of providers) {
    const cat = guessCategory(p.provider);
    if (!providerGroups.has(cat)) providerGroups.set(cat, []);
    providerGroups.get(cat)!.push(p);
  }

  return (
    <section style={{
      background: 'rgba(255, 255, 255, 0.03)',
      border: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: '16px',
      padding: '20px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px' }}>
        <div>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>🔐 API Keys & Providers</h2>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>
            Provider authentication status. Keys are configured in <code style={{ color: '#94a3b8', background: 'rgba(0,0,0,0.3)', padding: '1px 4px', borderRadius: '3px' }}>~/.hermes/.env</code>. Less common providers and base URL overrides are available under <strong>Show Advanced</strong>.
          </p>
        </div>
        <button
          onClick={() => setConfigModalCategories(['provider', 'tool', 'messaging'])}
          style={{
            padding: '6px 12px',
            borderRadius: '6px',
            background: 'rgba(255, 255, 255, 0.1)',
            border: '1px solid rgba(255, 255, 255, 0.2)',
            color: '#e2e8f0',
            cursor: 'pointer',
            fontSize: '0.85rem'
          }}
        >
          Edit Keys
        </button>
      </div>

      {activeProvider && (
        <div style={{
          padding: '10px 14px',
          background: 'rgba(99, 102, 241, 0.08)',
          border: '1px solid rgba(99, 102, 241, 0.2)',
          borderRadius: '10px',
          marginBottom: '16px',
          fontSize: '0.85rem',
          color: '#a5b4fc',
        }}>
          Active provider: <strong>{activeProvider}</strong>
        </div>
      )}

      {CATEGORY_ORDER.map(cat => {
        const group = providerGroups.get(cat);
        if (!group || group.length === 0) return null;

        return (
          <div key={cat} style={{ marginBottom: '20px' }}>
            <h3 style={{ 
              margin: '0 0 12px 0', 
              color: '#94a3b8', 
              fontSize: '0.8rem', 
              textTransform: 'uppercase', 
              letterSpacing: '0.5px' 
            }}>
              {CATEGORY_LABELS[cat] || cat}
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '10px' }}>
              {group.map(p => {
                const loggedIn = Boolean(p.status?.logged_in);
                const isActive = p.active;
                const baseUrl = typeof p.status?.base_url === 'string' ? p.status.base_url : '';
                const keySource = typeof p.status?.key_source === 'string' ? p.status.key_source : '';
                const authTypeLabel = formatAuthType(p.auth_type);

                return (
                  <div 
                    key={p.provider} 
                    style={{ 
                      padding: '12px 14px', 
                      background: isActive ? 'rgba(99, 102, 241, 0.06)' : 'rgba(0,0,0,0.15)',
                      border: `1px solid ${isActive ? 'rgba(99, 102, 241, 0.2)' : loggedIn ? 'rgba(34, 197, 94, 0.15)' : 'rgba(255,255,255,0.05)'}`,
                      borderRadius: '10px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '10px',
                    }}
                  >
                    <div style={{
                      width: '8px',
                      height: '8px',
                      borderRadius: '50%',
                      background: loggedIn ? '#22c55e' : '#475569',
                      flexShrink: 0,
                      boxShadow: loggedIn ? '0 0 6px rgba(34, 197, 94, 0.4)' : 'none',
                    }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ 
                        fontSize: '0.875rem', 
                        fontWeight: 600, 
                        color: '#e2e8f0',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {p.name || p.provider}
                      </div>
                      <div style={{ fontSize: '0.7rem', color: '#64748b' }}>
                        {loggedIn ? 'Configured' : 'Not configured'}
                        {isActive && ' · Active'}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginTop: '6px' }}>
                        {authTypeLabel && (
                          <span style={{
                            fontSize: '0.65rem',
                            padding: '2px 6px',
                            borderRadius: '999px',
                            background: 'rgba(148, 163, 184, 0.12)',
                            color: '#cbd5e1',
                          }}>
                            {authTypeLabel}
                          </span>
                        )}
                        {keySource && (
                          <span style={{
                            fontSize: '0.65rem',
                            padding: '2px 6px',
                            borderRadius: '999px',
                            background: 'rgba(56, 189, 248, 0.08)',
                            color: '#7dd3fc',
                          }}>
                            {keySource}
                          </span>
                        )}
                      </div>
                      {baseUrl && (
                        <div style={{
                          fontSize: '0.66rem',
                          color: '#94a3b8',
                          marginTop: '6px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}>
                          {baseUrl}
                        </div>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{
                        fontSize: '0.7rem',
                        padding: '2px 6px',
                        borderRadius: '4px',
                        background: loggedIn ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                        color: loggedIn ? '#4ade80' : '#f87171',
                      }}>
                        {loggedIn ? '✓' : '✗'}
                      </span>
                      {p.provider === 'openai-codex' && (
                        <button
                          onClick={() => setShowCodexModal(true)}
                          style={{
                            background: 'rgba(56, 189, 248, 0.1)',
                            border: '1px solid rgba(56, 189, 248, 0.3)',
                            color: '#38bdf8',
                            borderRadius: '4px',
                            padding: '2px 8px',
                            fontSize: '0.7rem',
                            cursor: 'pointer',
                          }}
                        >
                          Manage
                        </button>
                      )}
                      {p.provider !== 'openai-codex' && (
                        <button
                          onClick={() => setConfigModalCategories(['provider'])}
                          style={{
                            background: 'rgba(255, 255, 255, 0.06)',
                            border: '1px solid rgba(255, 255, 255, 0.12)',
                            color: '#cbd5e1',
                            borderRadius: '4px',
                            padding: '2px 8px',
                            fontSize: '0.7rem',
                            cursor: 'pointer',
                          }}
                        >
                          Edit
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {/* Show any uncategorized providers */}
      {Array.from(providerGroups.entries())
        .filter(([cat]) => !CATEGORY_ORDER.includes(cat))
        .map(([cat, group]) => (
          <div key={cat} style={{ marginBottom: '20px' }}>
            <h3 style={{ margin: '0 0 12px 0', color: '#94a3b8', fontSize: '0.8rem', textTransform: 'uppercase' }}>
              {cat}
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '10px' }}>
              {group.map(p => {
                const loggedIn = Boolean(p.status?.logged_in);
                return (
                  <div key={p.provider} style={{ padding: '12px 14px', background: 'rgba(0,0,0,0.15)', border: '1px solid rgba(255,255,255,0.05)', borderRadius: '10px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: loggedIn ? '#22c55e' : '#475569', flexShrink: 0 }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '0.875rem', fontWeight: 600, color: '#e2e8f0' }}>{p.name || p.provider}</div>
                      <div style={{ fontSize: '0.7rem', color: '#64748b' }}>{loggedIn ? 'Configured' : 'Not configured'}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        
      {configModalCategories && (
        <ApiKeyConfigModal
          categories={configModalCategories}
          onClose={() => setConfigModalCategories(null)}
          onSaved={() => {
            setConfigModalCategories(null);
            fetchAuth();
          }}
        />
      )}

      {showCodexModal && (
        <CodexAuthModal onClose={() => { setShowCodexModal(false); fetchAuth(); }} />
      )}
    </section>
  );
}

function guessCategory(providerId: string): string {
  const lower = providerId.toLowerCase();
  // Messaging platforms
  if (['telegram', 'discord', 'slack', 'whatsapp', 'signal', 'matrix', 'mattermost', 'homeassistant', 'feishu', 'wecom'].some(p => lower.includes(p))) {
    return 'messaging';
  }
  // Tool-specific keys  
  if (['browserbase', 'firecrawl', 'exa', 'fal', 'elevenlabs', 'tavily', 'parallel', 'tinker', 'wandb', 'github', 'honcho', 'camofox', 'browser_use'].some(t => lower.includes(t))) {
    return 'tool';
  }
  // Everything else is a provider
  return 'provider';
}

function formatAuthType(authType: string | null): string {
  if (!authType) {
    return '';
  }
  switch (authType) {
    case 'api_key':
      return 'API Key';
    case 'oauth_device_code':
      return 'Device Code';
    case 'oauth_external':
      return 'External OAuth';
    case 'external_process':
      return 'Local Process';
    default:
      return authType.replace(/_/g, ' ');
  }
}
