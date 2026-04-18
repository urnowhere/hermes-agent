import { useEffect, useRef, useState } from 'react';
import { TerminalHost } from './TerminalHost';
import { openSessionEventStream, type GuiEvent } from '../../lib/events';

/**
 * TerminalPanel renders a live feed of all tool executions from the agent's
 * event stream. It subscribes to the current session's SSE and formats
 * tool.started / tool.completed events as terminal-style log lines.
 */
export function TerminalPanel() {
  const [lines, setLines] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState<string>('current');
  const subRef = useRef<{ close(): void } | null>(null);

  // Listen for the active session from ChatPage
  useEffect(() => {
    const handleSessionSync = (e: any) => {
      if (e.detail?.sessionId) {
        setSessionId(e.detail.sessionId);
      }
    };
    window.addEventListener('hermes-session-sync', handleSessionSync);
    // Request the current session
    window.dispatchEvent(new CustomEvent('hermes-run-request-sync'));
    return () => window.removeEventListener('hermes-session-sync', handleSessionSync);
  }, []);

  // Subscribe to the event stream and format tool events as terminal lines
  useEffect(() => {
    if (subRef.current) {
      subRef.current.close();
    }

    const formatTimestamp = () => {
      const now = new Date();
      return `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`;
    };

    const handleEvent = (event: GuiEvent) => {
      const ts = formatTimestamp();

      if (event.type === 'run.started') {
        setLines(prev => [...prev,
          '',
          `\x1b[1;36m━━━ Run Started ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\x1b[0m`,
          `\x1b[90m${ts}\x1b[0m  Run ID: \x1b[33m${event.run_id}\x1b[0m`,
        ]);
      }

      if (event.type === 'tool.started') {
        const name = String(event.payload.tool_name ?? 'unknown');
        const preview = String(event.payload.preview ?? '').slice(0, 120);
        setLines(prev => [...prev,
          `\x1b[90m${ts}\x1b[0m  \x1b[1;34m▶\x1b[0m \x1b[1m${name}\x1b[0m${preview ? `  \x1b[90m${preview}\x1b[0m` : ''}`,
        ]);
      }

      if (event.type === 'tool.completed') {
        const name = String(event.payload.tool_name ?? 'unknown');
        const duration = event.payload.duration != null ? `${event.payload.duration}` : '';
        const resultPreview = String(event.payload.result_preview ?? '').slice(0, 200);
        const failed = event.payload.is_error === true;
        const marker = failed ? '\x1b[1;31m✗\x1b[0m' : '\x1b[1;32m✓\x1b[0m';
        const durationStr = duration ? ` \x1b[90m(${duration})\x1b[0m` : '';
        setLines(prev => [...prev,
          `\x1b[90m${ts}\x1b[0m  ${marker} \x1b[1m${name}\x1b[0m${durationStr}`,
          ...(resultPreview ? [`       \x1b[90m${resultPreview}\x1b[0m`] : []),
        ]);
      }

      if (event.type === 'run.completed' || event.type === 'run.failed') {
        const ok = event.type === 'run.completed';
        const statusLabel = ok ? '\x1b[1;32m✓ Run Completed\x1b[0m' : `\x1b[1;31m✗ Run Failed\x1b[0m`;
        setLines(prev => [...prev,
          `\x1b[90m${ts}\x1b[0m  ${statusLabel}`,
          `\x1b[1;36m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\x1b[0m`,
          '',
        ]);
      }

      if (event.type === 'message.assistant.completed') {
        const preview = String(event.payload.content ?? '').slice(0, 100);
        if (preview) {
          setLines(prev => [...prev,
            `\x1b[90m${ts}\x1b[0m  \x1b[35m◀\x1b[0m Assistant response (${preview.length} chars)`,
          ]);
        }
      }
    };

    subRef.current = openSessionEventStream(sessionId, handleEvent);

    return () => {
      subRef.current?.close();
      subRef.current = null;
    };
  }, [sessionId]);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ 
        padding: '6px 16px', 
        background: 'rgba(0,0,0,0.2)', 
        borderBottom: '1px solid rgba(255,255,255,0.05)', 
        fontSize: '0.8rem', 
        color: '#94a3b8', 
        display: 'flex', 
        justifyContent: 'space-between', 
        alignItems: 'center' 
      }}>
        <span>Tool Execution Feed</span>
        <button
          onClick={() => setLines([])}
          style={{ 
            background: 'transparent', 
            border: '1px solid rgba(255,255,255,0.1)', 
            color: '#64748b', 
            padding: '2px 8px', 
            borderRadius: '4px', 
            cursor: 'pointer', 
            fontSize: '0.75rem' 
          }}
        >
          Clear
        </button>
      </div>
      <div style={{ flex: 1, position: 'relative' }}>
        <div style={{ position: 'absolute', inset: 0 }}>
          <TerminalHost logs={lines} />
        </div>
      </div>
    </div>
  );
}
