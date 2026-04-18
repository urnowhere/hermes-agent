interface RunStatusBarProps {
  status: string;
  sessionId?: string;
  model?: string;
}

function statusColor(status: string): string {
  switch (status) {
    case 'ready': return '#4ade80';
    case 'running': case 'sending…': return '#60a5fa';
    case 'completed': return '#a78bfa';
    case 'error': case 'failed': case 'disconnected': return '#f87171';
    case 'connecting': return '#fbbf24';
    default: return '#94a3b8';
  }
}

export function RunStatusBar({ status, sessionId = 'new session', model = 'hermes-agent' }: RunStatusBarProps) {
  return (
    <section className="run-status" aria-label="Run status">
      <span className="status-pill" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: statusColor(status), display: 'inline-block', flexShrink: 0 }} />
        {status}
      </span>
      <span className="status-pill">Session: {sessionId}</span>
      <span className="status-pill">Model: {model}</span>
    </section>
  );
}
