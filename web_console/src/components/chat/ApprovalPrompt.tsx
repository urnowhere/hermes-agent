interface ApprovalPromptProps {
  pending: Array<{ id: string; command?: string }>;
  onApprove: (id: string, decision: 'once' | 'session' | 'always') => void;
  onDeny: (id: string) => void;
}

export function ApprovalPrompt({ pending, onApprove, onDeny }: ApprovalPromptProps) {
  if (pending.length === 0) return null;

  const request = pending[0]; // Process one at a time for clarity

  return (
    <div style={{
      position: 'fixed',
      bottom: '140px',
      left: '50%',
      transform: 'translateX(-50%)',
      background: 'rgba(239, 68, 68, 0.15)',
      backdropFilter: 'blur(16px)',
      border: '1px solid rgba(239, 68, 68, 0.3)',
      boxShadow: '0 12px 40px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(239, 68, 68, 0.1)',
      padding: '24px',
      borderRadius: '24px',
      zIndex: 100,
      minWidth: '500px',
      display: 'flex',
      flexDirection: 'column',
      gap: '16px',
      animation: 'modalScaleIn 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
    }}>
      <h3 style={{ margin: 0, color: '#f87171', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{ fontSize: '1.2rem' }}>⚠️</span> Action Required
      </h3>
      <p style={{ margin: 0, color: '#fca5a5', lineHeight: 1.5 }}>
        Hermes wants to run the following command. Do you approve?
      </p>

      {request.command && (
        <pre style={{ margin: 0, padding: '16px', background: 'rgba(0, 0, 0, 0.3)', color: '#f8f8f2', borderRadius: '12px', border: '1px solid rgba(255, 255, 255, 0.1)', overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontFamily: 'monospace', fontSize: '14px' }}>
          {request.command}
        </pre>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px', marginTop: '4px' }}>
        <button onClick={() => onApprove(request.id, 'once')} type="button" style={{ background: 'rgba(239, 68, 68, 0.2)', color: '#fca5a5', border: '1px solid rgba(239, 68, 68, 0.3)', padding: '12px', borderRadius: '14px', cursor: 'pointer', fontWeight: 500, transition: 'all 0.2s' }}>
          Approve Once
        </button>
        <button onClick={() => onApprove(request.id, 'session')} type="button" style={{ background: 'rgba(34, 197, 94, 0.2)', color: '#86efac', border: '1px solid rgba(34, 197, 94, 0.3)', padding: '12px', borderRadius: '14px', cursor: 'pointer', fontWeight: 500, transition: 'all 0.2s' }}>
          Allow for Session
        </button>
        <button onClick={() => onDeny(request.id)} type="button" style={{ background: 'rgba(0, 0, 0, 0.3)', color: '#9ca3af', border: '1px solid rgba(255, 255, 255, 0.1)', padding: '12px', borderRadius: '14px', cursor: 'pointer', fontWeight: 500, transition: 'all 0.2s' }}>
          Deny
        </button>
      </div>
    </div>
  );
}
