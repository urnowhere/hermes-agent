interface CheckpointListProps {
  checkpoints: Array<{ id: string; label: string }>;
  onRollback?: (checkpointId: string) => Promise<void>;
}

export function CheckpointList({ checkpoints, onRollback }: CheckpointListProps) {
  const btnStyle: React.CSSProperties = {
    background: 'rgba(251, 191, 36, 0.15)',
    border: '1px solid rgba(251, 191, 36, 0.3)',
    color: '#fde68a',
    padding: '3px 10px',
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
    }} aria-label="Checkpoint list">
      <div style={{ marginBottom: '10px' }}>
        <h3 style={{ margin: 0, color: '#e2e8f0', fontSize: '0.95rem' }}>📌 Checkpoints</h3>
      </div>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {checkpoints.map((cp) => (
          <li key={cp.id} style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '8px 10px', borderRadius: '8px',
            background: 'rgba(255, 255, 255, 0.03)',
          }}>
            <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>{cp.label}</span>
            {onRollback && (
              <button type="button" onClick={() => onRollback(cp.id)} style={btnStyle} title="Rollback to this checkpoint">
                ↩ Rollback
              </button>
            )}
          </li>
        ))}
        {checkpoints.length === 0 && (
          <p style={{ color: '#475569', fontSize: '0.8rem', textAlign: 'center', padding: '8px' }}>No checkpoints.</p>
        )}
      </ul>
    </section>
  );
}
