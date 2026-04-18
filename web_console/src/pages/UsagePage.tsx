import { useState, useEffect } from 'react';
import { apiClient } from '../lib/api';
import { toastStore } from '../store/toastStore';
import { ErrorBoundary } from '../components/shared/ErrorBoundary';
import { LoadingSpinner } from '../components/shared/LoadingSpinner';

interface UsageInsightData {
  days: number;
  empty: boolean;
  generated_at?: number;
  overview: Record<string, any>;
  models: { model: string; sessions: number; total_tokens: number; cost?: number; }[];
  platforms: { platform: string; sessions: number; messages: number; }[];
  tools: { tool: string; count: number; percentage: number; }[];
  activity: Record<string, any>;
  top_sessions: { label: string; value: string; date: string; session_id: string }[];
}

interface UsageInsightsResponse {
  ok: boolean;
  report: UsageInsightData;
  error?: string;
}

export function UsagePage() {
  const [report, setReport] = useState<UsageInsightData | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);

  const fetchInsights = async () => {
    setLoading(true);
    try {
      const res = await apiClient.get<UsageInsightsResponse>(`/usage/insights?days=${days}`);
      if (res?.ok && res.report) {
        setReport(res.report);
      } else {
        toastStore.error(res?.error || 'Failed to load usage insights');
      }
    } catch (e) {
      console.error('Insights fetch error:', e);
      toastStore.error('Network error loading insights');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchInsights();
  }, [days]);

  return (
    <ErrorBoundary>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', maxWidth: '900px', margin: '0 auto', padding: '20px 0', flex: 1, overflowY: 'auto', minHeight: 0, width: '100%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h1 style={{ margin: 0, color: '#f8fafc', fontSize: '1.5rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
              📊 Usage Insights
            </h1>
            <p style={{ margin: '4px 0 0', color: '#94a3b8', fontSize: '0.9rem' }}>
              Analytics on tokens, models, tools, and overall activity.
            </p>
          </div>
          <div>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              style={{
                background: 'rgba(30, 41, 59, 0.8)',
                color: '#e2e8f0',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                borderRadius: '8px',
                padding: '8px 12px',
                fontSize: '0.9rem',
                outline: 'none',
                cursor: 'pointer'
              }}
            >
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
              <option value="365">Last 365 days</option>
            </select>
          </div>
        </div>

        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '60px' }}>
            <LoadingSpinner size="md" />
          </div>
        ) : !report || report.empty ? (
          <div style={{ textAlign: 'center', padding: '60px', color: '#94a3b8', background: 'rgba(30, 41, 59, 0.4)', borderRadius: '16px' }}>
            <div style={{ fontSize: '2rem', marginBottom: '12px' }}>📉</div>
            No data available for the selected period.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            {/* Overview */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
              <div style={{ background: 'rgba(30, 41, 59, 0.4)', padding: '20px', borderRadius: '16px', border: '1px solid rgba(255, 255, 255, 0.05)' }}>
                <div style={{ color: '#94a3b8', fontSize: '0.85rem', marginBottom: '8px', textTransform: 'uppercase' }}>Total Tokens</div>
                <div style={{ color: '#f8fafc', fontSize: '1.8rem', fontWeight: 600 }}>{report.overview.total_tokens?.toLocaleString() || 0}</div>
                <div style={{ color: '#64748b', fontSize: '0.8rem', marginTop: '4px' }}>
                  {report.overview.total_input_tokens?.toLocaleString() || 0} in / {report.overview.total_output_tokens?.toLocaleString() || 0} out
                </div>
              </div>
              <div style={{ background: 'rgba(30, 41, 59, 0.4)', padding: '20px', borderRadius: '16px', border: '1px solid rgba(255, 255, 255, 0.05)' }}>
                <div style={{ color: '#94a3b8', fontSize: '0.85rem', marginBottom: '8px', textTransform: 'uppercase' }}>Estimated Cost</div>
                <div style={{ color: '#4ade80', fontSize: '1.8rem', fontWeight: 600 }}>${report.overview.estimated_cost?.toFixed(2) || '0.00'}</div>
                <div style={{ color: '#64748b', fontSize: '0.8rem', marginTop: '4px' }}>
                  {report.overview.models_without_pricing?.length ? '* excludes custom models' : 'Based on known pricing'}
                </div>
              </div>
              <div style={{ background: 'rgba(30, 41, 59, 0.4)', padding: '20px', borderRadius: '16px', border: '1px solid rgba(255, 255, 255, 0.05)' }}>
                <div style={{ color: '#94a3b8', fontSize: '0.85rem', marginBottom: '8px', textTransform: 'uppercase' }}>Sessions</div>
                <div style={{ color: '#f8fafc', fontSize: '1.8rem', fontWeight: 600 }}>{report.overview.total_sessions?.toLocaleString() || 0}</div>
                <div style={{ color: '#64748b', fontSize: '0.8rem', marginTop: '4px' }}>
                  {report.overview.total_messages?.toLocaleString() || 0} messages total
                </div>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '24px' }}>
              {/* Models Breakdown */}
              {report.models && report.models.length > 0 && (
                <div style={{ background: 'rgba(30, 41, 59, 0.4)', padding: '20px', borderRadius: '16px', border: '1px solid rgba(255, 255, 255, 0.05)' }}>
                  <h3 style={{ margin: '0 0 16px', color: '#e2e8f0', fontSize: '1.1rem' }}>🤖 Models Usage</h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    {report.models.slice(0, 5).map((m: any) => (
                      <div key={m.model} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div>
                          <div style={{ color: '#f8fafc', fontSize: '0.95rem' }}>{m.model}</div>
                          <div style={{ color: '#64748b', fontSize: '0.8rem' }}>{m.sessions} sessions</div>
                        </div>
                        <div style={{ textAlign: 'right' }}>
                          <div style={{ color: '#e2e8f0', fontSize: '0.9rem' }}>{m.total_tokens.toLocaleString()} tkns</div>
                          <div style={{ color: m.cost ? '#4ade80' : '#94a3b8', fontSize: '0.8rem' }}>{m.cost ? `$${m.cost.toFixed(2)}` : 'N/A'}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Tools Breakdown */}
              {report.tools && report.tools.length > 0 && (
                <div style={{ background: 'rgba(30, 41, 59, 0.4)', padding: '20px', borderRadius: '16px', border: '1px solid rgba(255, 255, 255, 0.05)' }}>
                  <h3 style={{ margin: '0 0 16px', color: '#e2e8f0', fontSize: '1.1rem' }}>🔧 Top Tools</h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    {report.tools.slice(0, 6).map((t: any) => (
                      <div key={t.tool} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <div style={{ width: '4px', height: '16px', background: '#38bdf8', borderRadius: '2px', opacity: Math.max(0.2, t.percentage / 100) }} />
                          <span style={{ color: '#f8fafc', fontSize: '0.95rem' }}>{t.tool}</span>
                        </div>
                        <div style={{ textAlign: 'right' }}>
                          <div style={{ color: '#e2e8f0', fontSize: '0.9rem' }}>{t.count.toLocaleString()} calls</div>
                          <div style={{ color: '#64748b', fontSize: '0.8rem' }}>{t.percentage.toFixed(1)}%</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            
            {/* Top Sessions */}
            {report.top_sessions && report.top_sessions.length > 0 && (
              <div style={{ background: 'rgba(30, 41, 59, 0.4)', padding: '20px', borderRadius: '16px', border: '1px solid rgba(255, 255, 255, 0.05)' }}>
                <h3 style={{ margin: '0 0 16px', color: '#e2e8f0', fontSize: '1.1rem' }}>🏆 Notable Sessions</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
                  {report.top_sessions.map((ts: any) => (
                    <div key={ts.label} style={{ background: 'rgba(0,0,0,0.2)', padding: '16px', borderRadius: '12px' }}>
                      <div style={{ color: '#818cf8', fontSize: '0.85rem', marginBottom: '8px', textTransform: 'uppercase' }}>{ts.label}</div>
                      <div style={{ color: '#f8fafc', fontSize: '1.2rem', fontWeight: 500 }}>{ts.value}</div>
                      <div style={{ color: '#64748b', fontSize: '0.8rem', marginTop: '4px' }}>
                        {ts.date} • <span style={{ fontFamily: 'monospace' }}>{ts.session_id.substring(0, 8)}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </ErrorBoundary>
  );
}
