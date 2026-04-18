import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';
import { TerminalHost } from './TerminalHost';

interface BackgroundProcess {
  process_id: string;
  command: string;
  status: string;
  started_at: number;
}

export function ProcessPanel() {
  const [processes, setProcesses] = useState<BackgroundProcess[]>([]);
  const [activeProcessId, setActiveProcessId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  // Fetch process list
  const fetchProcesses = async () => {
    try {
      const res = await apiClient.get<{ ok: boolean; processes: BackgroundProcess[] }>('/processes');
      if (res.ok && res.processes) {
        setProcesses(res.processes);
        if (!activeProcessId && res.processes.length > 0) {
          setActiveProcessId(res.processes[0].process_id);
        }
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProcesses();
    const interval = setInterval(fetchProcesses, 3000);
    return () => clearInterval(interval);
  }, []);

  // Fetch active process logs
  useEffect(() => {
    if (!activeProcessId) {
      setLogs([]);
      return;
    }

    let active = true;
    const fetchLogs = async () => {
      try {
        const res = await apiClient.get<{ ok: boolean; log_lines: string[] }>(`/processes/${activeProcessId}/log?offset=0&limit=500`);
        if (active && res.ok && res.log_lines) {
          setLogs(res.log_lines);
        }
      } catch (err) {
        console.error(err);
      }
    };

    fetchLogs();
    const interval = setInterval(fetchLogs, 1500);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [activeProcessId]);

  const handleKill = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await apiClient.post(`/processes/${id}/kill`);
      fetchProcesses();
    } catch (err) {
      console.error(err);
    }
  };

  if (loading && processes.length === 0) {
    return <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>Loading processes...</div>;
  }

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%' }}>
      {/* Sidebar for list of processes */}
      <div style={{ width: '300px', borderRight: '1px solid rgba(255,255,255,0.05)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '12px 16px', background: 'rgba(0,0,0,0.2)', borderBottom: '1px solid rgba(255,255,255,0.05)', fontSize: '0.85rem', color: '#94a3b8' }}>
          Background Processes
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {processes.length === 0 ? (
            <div style={{ padding: '24px', color: '#64748b', textAlign: 'center', fontSize: '0.875rem' }}>No processes running.</div>
          ) : (
            processes.map(p => (
              <div 
                key={p.process_id} 
                onClick={() => setActiveProcessId(p.process_id)}
                style={{ 
                  padding: '12px 16px', 
                  borderBottom: '1px solid rgba(255,255,255,0.05)',
                  background: activeProcessId === p.process_id ? 'rgba(56, 189, 248, 0.1)' : 'transparent',
                  cursor: 'pointer',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.875rem', fontWeight: 600, color: '#e2e8f0', fontFamily: 'monospace', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '180px' }}>
                    {p.command}
                  </span>
                  <span style={{ fontSize: '0.7rem', padding: '2px 6px', borderRadius: '4px', background: p.status === 'running' ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)', color: p.status === 'running' ? '#4ade80' : '#f87171' }}>
                    {p.status}
                  </span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '0.75rem', color: '#64748b' }}>
                    {new Date(p.started_at * 1000).toLocaleTimeString()}
                  </span>
                  {p.status === 'running' && (
                    <button 
                      onClick={(e) => handleKill(p.process_id, e)}
                      style={{ background: 'transparent', border: 'none', color: '#f87171', fontSize: '0.75rem', cursor: 'pointer', padding: '0' }}
                    >
                      Kill
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
      
      {/* Main area for specific terminal output */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative' }}>
        {activeProcessId ? (
          <div style={{ position: 'absolute', inset: 0 }}>
            <TerminalHost logs={logs} />
          </div>
        ) : (
          <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', color: '#64748b', fontSize: '0.875rem' }}>
            Select a process to view its output
          </div>
        )}
      </div>
    </div>
  );
}
