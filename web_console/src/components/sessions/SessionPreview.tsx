import { useState } from 'react';
import { apiClient } from '../../lib/api';
import { getBackendUrl } from '../../store/backendStore';
import { toastStore } from '../../store/toastStore';

interface SessionPreviewProps {
  title: string;
  summary: string;
  transcript: string[];
  sessionId?: string;
}

export function SessionPreview({ title, summary, transcript, sessionId }: SessionPreviewProps) {
  const [exporting, setExporting] = useState(false);

  const handleExport = async (format: 'md' | 'json' | 'txt') => {
    if (!sessionId) return;
    setExporting(true);
    try {
      // In a real app we might use window.location.href to trigger a download,
      // but Since apiClient does not support blob responses intuitively, we can fetch
      // it and then create a blob URL to download.
      const response = await fetch(`${getBackendUrl()}/api/gui/sessions/${sessionId}/export?format=${format}`);
      if (!response.ok) throw new Error('Export failed to generate on server.');
      const blob = await response.blob();
      
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `session_${sessionId}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      
      toastStore.success(`Session exported as ${format.toUpperCase()}`);
    } catch (err) {
      toastStore.error('Export Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setExporting(false);
    }
  };

  return (
    <section className="sessions-card" aria-label="Session preview">
      <div className="sessions-card-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h2>{title}</h2>
            <p>{summary}</p>
          </div>
          {sessionId && transcript.length > 0 && (
            <div style={{ display: 'flex', gap: '8px', zIndex: 10 }}>
              <button 
                disabled={exporting}
                onClick={() => handleExport('md')}
                style={{
                  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
                  color: '#e2e8f0', padding: '6px 12px', borderRadius: '8px', fontSize: '0.8rem',
                  cursor: exporting ? 'wait' : 'pointer'
                }}
              >
                ⬇️ MD
              </button>
              <button 
                disabled={exporting}
                onClick={() => handleExport('json')}
                style={{
                  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
                  color: '#e2e8f0', padding: '6px 12px', borderRadius: '8px', fontSize: '0.8rem',
                  cursor: exporting ? 'wait' : 'pointer'
                }}
              >
                ⬇️ JSON
              </button>
              <button 
                disabled={exporting}
                onClick={() => handleExport('txt')}
                style={{
                  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
                  color: '#e2e8f0', padding: '6px 12px', borderRadius: '8px', fontSize: '0.8rem',
                  cursor: exporting ? 'wait' : 'pointer'
                }}
              >
                ⬇️ TXT
              </button>
            </div>
          )}
        </div>
      </div>
      <div className="session-preview-transcript">
        {transcript.map((line) => (
          <div key={line} className="session-preview-line">
            {line}
          </div>
        ))}
      </div>
    </section>
  );
}
