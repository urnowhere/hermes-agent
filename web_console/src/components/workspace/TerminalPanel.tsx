import { useEffect, useRef, useState } from 'react';
import { apiClient } from '../../lib/api';
import { TerminalHost } from '../drawer/TerminalHost';

interface TerminalOutput {
  ok: boolean;
  output?: string[];
}

export function TerminalPanel() {
  const [lines, setLines] = useState<string[]>([]);
  const [command, setCommand] = useState('');
  const [running, setRunning] = useState(false);

  const handleExec = async () => {
    const cmd = command.trim();
    if (!cmd || running) return;

    setRunning(true);
    setLines(prev => [...prev, `\x1b[36m$ ${cmd}\x1b[0m`]);
    setCommand('');

    try {
      const res = await apiClient.post<TerminalOutput>('/workspace/exec', { command: cmd });
      if (res.ok && res.output) {
        setLines(prev => [...prev, ...res.output!]);
      } else {
        setLines(prev => [...prev, '\x1b[33m(no output)\x1b[0m']);
      }
    } catch (err) {
      setLines(prev => [...prev, `\x1b[31mError: ${err instanceof Error ? err.message : 'command failed'}\x1b[0m`]);
    } finally {
      setRunning(false);
    }
  };

  return (
    <section style={{
      background: 'rgba(255, 255, 255, 0.03)',
      border: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: '14px',
      overflow: 'hidden',
      display: 'flex',
      flexDirection: 'column',
    }} aria-label="Terminal panel">
      <div style={{ padding: '10px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h3 style={{ margin: 0, color: '#e2e8f0', fontSize: '0.95rem' }}>⌨️ Terminal</h3>
        {lines.length > 0 && (
          <button
            type="button"
            onClick={() => setLines([])}
            style={{
              background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
              color: '#64748b', borderRadius: '6px', padding: '3px 8px', cursor: 'pointer', fontSize: '0.7rem',
            }}
          >
            Clear
          </button>
        )}
      </div>
      <div style={{ height: '200px', position: 'relative' }}>
        <div style={{ position: 'absolute', inset: 0 }}>
          <TerminalHost logs={lines} />
        </div>
      </div>
      <div style={{
        display: 'flex', gap: '6px', padding: '8px 10px',
        borderTop: '1px solid rgba(255,255,255,0.06)',
        background: 'rgba(0,0,0,0.15)',
      }}>
        <span style={{ color: '#818cf8', fontSize: '0.85rem', lineHeight: '32px' }}>$</span>
        <input
          type="text"
          value={command}
          onChange={e => setCommand(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleExec(); }}
          placeholder="Enter command…"
          disabled={running}
          style={{
            flex: 1, padding: '6px 10px', borderRadius: '6px',
            border: '1px solid rgba(129,140,248,0.2)', background: 'rgba(0,0,0,0.2)',
            color: 'white', fontSize: '0.85rem', fontFamily: 'monospace',
          }}
        />
        <button
          type="button"
          onClick={handleExec}
          disabled={running || !command.trim()}
          style={{
            padding: '6px 14px', borderRadius: '6px', cursor: running ? 'default' : 'pointer',
            background: running ? 'rgba(255,255,255,0.04)' : 'rgba(129,140,248,0.15)',
            border: '1px solid rgba(129,140,248,0.3)',
            color: running ? '#475569' : '#a5b4fc', fontSize: '0.8rem',
          }}
        >
          {running ? '…' : 'Run'}
        </button>
      </div>
    </section>
  );
}
