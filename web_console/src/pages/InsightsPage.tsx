import React, { useState, useEffect } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip as RechartsTooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend
} from 'recharts';
import { getBackendUrl } from '../store/backendStore';
import './InsightsPage.css';

interface GlobalMetrics {
  token_usage_today: number;
  cost_today: number;
  active_processes: number;
  cron_jobs: number;
  cpu_percent: number;
  memory_percent: number;
  uptime_seconds: number;
}

interface InsightsReport {
  empty: boolean;
  days: number;
  overview: {
    total_sessions: number;
    total_messages: number;
    total_tool_calls: number;
    total_tokens: number;
    estimated_cost: number;
    total_hours: number;
    avg_session_duration: number;
  };
  models: { model: string; sessions: number; total_tokens: number; cost: number; has_pricing: boolean }[];
  platforms: { platform: string; sessions: number; messages: number; total_tokens: number }[];
  tools: { tool: string; count: number; percentage: number }[];
  activity: {
    by_day: { day: string; count: number }[];
    by_hour: { hour: number; count: number }[];
    active_days: number;
    max_streak: number;
  };
  top_sessions?: { label: string; session_id: string; value: string; date: string }[];
}

const COLORS = ['#4dabf7', '#69db7c', '#ff8787', '#ffd43b', '#da77f2', '#3bc9db'];

export function InsightsPage() {
  const [report, setReport] = useState<InsightsReport | null>(null);
  const [globalMetrics, setGlobalMetrics] = useState<GlobalMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState<number>(30);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function fetchMetrics() {
      try {
        const res = await fetch(`${getBackendUrl()}/api/gui/metrics/global`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.ok && active) {
          setGlobalMetrics(data.metrics);
        }
      } catch (err) {
        // ignore polling errors
      }
    }
    
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 3000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    async function loadData() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${getBackendUrl()}/api/gui/usage/insights?days=${days}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.ok) {
          setReport(data.report);
        } else {
          throw new Error(data.error || 'Failed to fetch insights');
        }
      } catch (err: any) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [days]);

  if (loading) {
    return (
      <div className="insights-loader">
        <div className="insights-spinner" />
        <span>Aggregating workspace telemetry...</span>
      </div>
    );
  }

  if (error) {
    return <div className="insights-page-container">Error: {error}</div>;
  }

  if (!report || report.empty) {
    return (
      <div className="insights-page-container">
        <div className="insights-header">
          <h2>Analytics & Insights</h2>
          <div className="insights-filters">
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={7}>Last 7 Days</option>
              <option value={30}>Last 30 Days</option>
              <option value={365}>All Time</option>
            </select>
          </div>
        </div>
        <div style={{ textAlign: 'center', marginTop: '3rem', color: 'var(--color-text-dim)' }}>
          No session history found for the selected timeframe.
        </div>
      </div>
    );
  }

  const { overview, tools, activity, top_sessions } = report;

  // Format activity data correctly based on how Recharts consumes it
  const dayActivityData = activity.by_day.map((d) => ({
    name: d.day,
    Count: d.count
  }));

  const toolDistData = tools.slice(0, 5).map((t, idx) => ({
    name: t.tool,
    value: t.count
  }));

  return (
    <div className="insights-page-container">
      <div className="insights-header">
        <h2>Dashboard & Analytics</h2>
        <div className="insights-filters">
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Last 7 Days</option>
            <option value={30}>Last 30 Days</option>
            <option value={365}>All Time</option>
          </select>
        </div>
      </div>

      {globalMetrics && (
        <div style={{ marginBottom: '24px', padding: '16px', background: 'rgba(59, 130, 246, 0.1)', border: '1px solid rgba(59, 130, 246, 0.3)', borderRadius: '12px' }}>
          <h3 style={{ margin: '0 0 16px 0', fontSize: '1rem', color: '#60a5fa', textTransform: 'uppercase', letterSpacing: '0.05em' }}>🔴 Live Command Center</h3>
          <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: '120px' }}>
               <div style={{ fontSize: '0.8rem', color: 'var(--color-text-dim)' }}>CPU Usage</div>
               <div style={{ fontSize: '1.4rem', fontWeight: 600 }}>{globalMetrics.cpu_percent.toFixed(1)}%</div>
            </div>
            <div style={{ flex: 1, minWidth: '120px' }}>
               <div style={{ fontSize: '0.8rem', color: 'var(--color-text-dim)' }}>Memory Usage</div>
               <div style={{ fontSize: '1.4rem', fontWeight: 600 }}>{globalMetrics.memory_percent.toFixed(1)}%</div>
            </div>
            <div style={{ flex: 1, minWidth: '120px' }}>
               <div style={{ fontSize: '0.8rem', color: 'var(--color-text-dim)' }}>Active Processes</div>
               <div style={{ fontSize: '1.4rem', fontWeight: 600, color: globalMetrics.active_processes > 0 ? '#4ade80' : 'inherit' }}>{globalMetrics.active_processes}</div>
            </div>
            <div style={{ flex: 1, minWidth: '120px' }}>
               <div style={{ fontSize: '0.8rem', color: 'var(--color-text-dim)' }}>Scheduled Cron Jobs</div>
               <div style={{ fontSize: '1.4rem', fontWeight: 600 }}>{globalMetrics.cron_jobs}</div>
            </div>
          </div>
        </div>
      )}

      <div className="insights-grid">
        <div className="insight-card">
          <span className="label">Total Sessions</span>
          <span className="value">{overview.total_sessions}</span>
          <span className="sub-value">{overview.total_messages} messages</span>
        </div>
        <div className="insight-card">
          <span className="label">Compute Cost</span>
          <span className="value">${overview.estimated_cost.toFixed(2)}</span>
          <span className="sub-value">{overview.total_tokens.toLocaleString()} tokens</span>
        </div>
        <div className="insight-card">
          <span className="label">Tool Execution</span>
          <span className="value">{overview.total_tool_calls.toLocaleString()}</span>
          <span className="sub-value">Actions invoked</span>
        </div>
        <div className="insight-card">
          <span className="label">Activity Streak</span>
          <span className="value">{activity.max_streak}</span>
          <span className="sub-value">Consecutive days active</span>
        </div>
      </div>

      <div className="insights-charts-row">
        <div className="chart-container">
          <h3 className="chart-header">Session History</h3>
          <div className="chart-wrapper">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dayActivityData}>
                <XAxis dataKey="name" stroke="var(--color-text-dim)" fontSize={12} tickLine={false} axisLine={false} />
                <RechartsTooltip cursor={{ fill: 'rgba(255, 255, 255, 0.05)' }} />
                <Bar dataKey="Count" fill="#4dabf7" radius={[4, 4, 0, 0]} barSize={32} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="chart-container">
          <h3 className="chart-header">Tools Distribution</h3>
          <div className="chart-wrapper">
            {toolDistData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={toolDistData}
                    cx="50%"
                    cy="50%"
                    innerRadius={50}
                    outerRadius={80}
                    paddingAngle={5}
                    dataKey="value"
                    stroke="none"
                  >
                    {toolDistData.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <RechartsTooltip />
                  <Legend verticalAlign="bottom" height={36} iconType="circle" />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--color-text-dim)' }}>
                No tool data available
              </div>
            )}
          </div>
        </div>
      </div>

      {top_sessions && top_sessions.length > 0 && (
        <div className="chart-container" style={{ marginTop: '0.5rem' }}>
          <h3 className="chart-header">Notable Sessions</h3>
          <div className="notable-sessions">
            {top_sessions.map((session, index) => (
              <div className="notable-session-item" key={index}>
                <div className="notable-label">
                  <span className="notable-name">{session.label}</span>
                  <span className="notable-date">{session.date} • {session.session_id}</span>
                </div>
                <div className="notable-value">{session.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
