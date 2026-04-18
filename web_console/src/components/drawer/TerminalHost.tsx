import { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

interface TerminalHostProps {
  logs: string[];
}

export function TerminalHost({ logs }: TerminalHostProps) {
  const terminalRef = useRef<HTMLDivElement>(null);
  const termInstance = useRef<Terminal | null>(null);
  const fitAddon = useRef<FitAddon | null>(null);
  const processedLogsCount = useRef(0);

  useEffect(() => {
    if (!terminalRef.current) return;

    const term = new Terminal({
      theme: {
        background: '#0f172a', // slate-900
        foreground: '#f8fafc',
        cursor: '#3b82f6',
      },
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      fontSize: 13,
      lineHeight: 1.2,
      disableStdin: true,
      cursorBlink: false,
      convertEol: true,
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    
    term.open(terminalRef.current);
    fit.fit();
    
    termInstance.current = term;
    fitAddon.current = fit;

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.current?.fit();
    });
    resizeObserver.observe(terminalRef.current);

    return () => {
      resizeObserver.disconnect();
      term.dispose();
      termInstance.current = null;
    };
  }, []);

  useEffect(() => {
    // Write new logs that haven't been written yet
    if (termInstance.current) {
      const newLogs = logs.slice(processedLogsCount.current);
      if (newLogs.length > 0) {
        for (const line of newLogs) {
          termInstance.current.writeln(line);
        }
        processedLogsCount.current = logs.length;
      }
    }
  }, [logs]);

  // Handle case where logs array gets completely reset
  useEffect(() => {
    if (logs.length === 0 && termInstance.current) {
      termInstance.current.clear();
      processedLogsCount.current = 0;
    }
  }, [logs.length]);

  return (
    <div 
      ref={terminalRef} 
      style={{ 
        width: '100%', 
        height: '100%', 
        overflow: 'hidden', 
        padding: '8px', 
        background: '#0f172a',
        borderRadius: '6px'
      }} 
    />
  );
}
