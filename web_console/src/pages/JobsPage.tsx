import { useEffect, useState } from 'react';
import { apiClient } from '../lib/api';
import { EmptyState } from '../components/shared/EmptyState';
import { LoadingSpinner } from '../components/shared/LoadingSpinner';

interface BackgroundRun {
  session_id: string;
  run_id: string;
  status: string;
  prompt: string;
  created_at: number;
}

export function JobsPage() {
  const [runs, setRuns] = useState<BackgroundRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRuns = async () => {
    try {
      const res = await apiClient.get<any>('/chat/backgrounds');
      if (res.ok) {
        setRuns(res.background_runs || []);
        setError(null);
      } else {
        setError(res.error || 'Failed to fetch background jobs.');
      }
    } catch (e: any) {
      setError(e.message || 'Error connecting to API.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRuns();
    // Poll every 5 seconds for background job updates
    const interval = setInterval(fetchRuns, 5000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return <div style={{ padding: '40px' }}><LoadingSpinner /></div>;
  }

  if (error) {
    return (
      <div style={{ padding: '40px' }}>
        <EmptyState title="Error Loading Jobs" description={error} icon="⚠️" />
        <button onClick={fetchRuns} style={{ marginTop: '16px', padding: '8px 16px', cursor: 'pointer' }}>Retry</button>
      </div>
    );
  }

  return (
    <div style={{ padding: '40px', maxWidth: '1000px', margin: '0 auto', flex: 1, overflowY: 'auto', minHeight: 0, width: '100%' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
        <div>
          <h1 style={{ fontSize: '2rem', fontWeight: 600, margin: '0 0 8px 0', color: '#f8fafc' }}>Background Jobs</h1>
          <p style={{ margin: 0, color: '#94a3b8' }}>Monitor the status of agents dispatched to the background.</p>
        </div>
        <button 
           onClick={() => fetchRuns()}
           style={{ padding: '8px 16px', borderRadius: '8px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', color: '#f8fafc', cursor: 'pointer' }}
        >
          🔄 Refresh
        </button>
      </header>

      {runs.length === 0 ? (
        <EmptyState 
           title="No Active Jobs" 
           description="Jobs dispatched smoothly to the background will appear here." 
           icon="💤" 
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {runs.map(run => (
            <div key={run.run_id} style={{
              background: 'rgba(15, 23, 42, 0.6)',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: '12px',
              padding: '20px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <h3 style={{ margin: 0, fontSize: '1.1rem', color: '#f1f5f9', flex: 1 }}>{run.prompt}</h3>
                <span style={{
                  padding: '4px 12px',
                  borderRadius: '99px',
                  fontSize: '0.85rem',
                  fontWeight: 600,
                  background: run.status === 'started' || run.status === 'running' ? 'rgba(56, 189, 248, 0.15)' : 
                              run.status === 'completed' ? 'rgba(74, 222, 128, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                  color: run.status === 'started' || run.status === 'running' ? '#7dd3fc' : 
                         run.status === 'completed' ? '#4ade80' : '#fca5a5',
                  textTransform: 'uppercase'
                }}>
                  {run.status}
                </span>
              </div>
              <div style={{ display: 'flex', gap: '24px', fontSize: '0.9rem', color: '#94a3b8' }}>
                <span><strong>Session DB:</strong> {run.session_id.substring(0, 8)}</span>
                <span><strong>Run ID:</strong> {run.run_id.substring(0, 8)}</span>
                <span><strong>Started:</strong> {new Date(run.created_at * 1000).toLocaleString()}</span>
              </div>
              
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '8px' }}>
                <a 
                  href={`#/chat/${run.session_id}`}
                  style={{ color: '#38bdf8', textDecoration: 'none', fontSize: '0.9rem', padding: '6px 12px', background: 'rgba(56, 189, 248, 0.1)', borderRadius: '6px' }}
                >
                  View Session →
                </a>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
