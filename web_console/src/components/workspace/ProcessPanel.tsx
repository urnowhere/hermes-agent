interface Process {
  id: string;
  status: string;
}

interface ProcessPanelProps {
  processes: Process[];
  onKill?: (processId: string) => Promise<void>;
  onViewLog?: (processId: string) => void;
}

export function ProcessPanel({ processes, onKill, onViewLog }: ProcessPanelProps) {
  const btnStyle: React.CSSProperties = {
    background: 'rgba(255, 255, 255, 0.06)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    padding: '3px 8px',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '0.75rem',
  };

  return (
    <section style={{
      background: 'rgba(255, 255, 255, 0.03)',
      border: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: '14px',
      padding: '14px',
    }} aria-label="Process panel">
      <div style={{ marginBottom: '10px' }}>
        <h3 style={{ margin: 0, color: '#e2e8f0', fontSize: '0.95rem' }}>⚡ Processes</h3>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {processes.map((p) => (
          <div key={p.id} style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '8px 10px', borderRadius: '8px',
            background: 'rgba(255, 255, 255, 0.03)',
          }}>
            <div>
              <code style={{ color: '#a5b4fc', fontSize: '0.8rem' }}>{p.id.slice(0, 12)}</code>
              <span style={{ color: p.status === 'running' ? '#86efac' : '#94a3b8', fontSize: '0.8rem', marginLeft: '8px' }}>
                {p.status}
              </span>
            </div>
            <div style={{ display: 'flex', gap: '4px' }}>
              {onViewLog && (
                <button type="button" onClick={() => onViewLog(p.id)} style={{ ...btnStyle, color: '#a5b4fc' }}>📋 Log</button>
              )}
              {onKill && p.status === 'running' && (
                <button type="button" onClick={() => onKill(p.id)} style={{ ...btnStyle, color: '#fca5a5' }}>✕ Kill</button>
              )}
            </div>
          </div>
        ))}
        {processes.length === 0 && (
          <p style={{ color: '#475569', fontSize: '0.8rem', textAlign: 'center', padding: '8px' }}>No background processes.</p>
        )}
      </div>
    </section>
  );
}
