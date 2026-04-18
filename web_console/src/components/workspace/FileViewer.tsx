import { useCallback, useEffect, useRef, useState } from 'react';
import { apiClient } from '../../lib/api';

interface FileViewerProps {
  path: string;
  content: string;
  onContentChange?: (newContent: string) => void;
}

export function FileViewer({ path, content, onContentChange }: FileViewerProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(content);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved' | 'error'>('idle');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Reset edit state when file changes
  useEffect(() => {
    setEditValue(content);
    setIsEditing(false);
    setSaveStatus('idle');
  }, [path, content]);

  const handleEdit = useCallback(() => {
    setIsEditing(true);
    setSaveStatus('idle');
    // Focus textarea after render
    setTimeout(() => textareaRef.current?.focus(), 50);
  }, []);

  const handleCancel = useCallback(() => {
    setEditValue(content);
    setIsEditing(false);
    setSaveStatus('idle');
  }, [content]);

  const handleSave = useCallback(async () => {
    if (!path) return;
    setSaving(true);
    setSaveStatus('idle');
    try {
      const res = await apiClient.post<{ ok: boolean }>('/workspace/file/save', {
        path,
        content: editValue,
      });
      if (res.ok) {
        setSaveStatus('saved');
        setIsEditing(false);
        onContentChange?.(editValue);
        setTimeout(() => setSaveStatus('idle'), 2500);
      } else {
        setSaveStatus('error');
      }
    } catch {
      setSaveStatus('error');
    } finally {
      setSaving(false);
    }
  }, [path, editValue, onContentChange]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Ctrl/Cmd+S to save
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        handleSave();
        return;
      }
      // Escape to cancel
      if (e.key === 'Escape') {
        handleCancel();
        return;
      }
      // Tab inserts two spaces
      if (e.key === 'Tab') {
        e.preventDefault();
        const textarea = e.currentTarget;
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const newValue = editValue.substring(0, start) + '  ' + editValue.substring(end);
        setEditValue(newValue);
        // Restore cursor position after React re-renders
        setTimeout(() => {
          textarea.selectionStart = textarea.selectionEnd = start + 2;
        }, 0);
      }
    },
    [handleSave, handleCancel, editValue],
  );

  const lines = (isEditing ? editValue : content).split('\n');
  const lineCount = lines.length;

  return (
    <section className="workspace-card file-viewer-card" aria-label="File viewer">
      <div className="workspace-card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2>{path}</h2>
          <p style={{ margin: 0, fontSize: '0.75rem', color: '#64748b' }}>
            {isEditing ? 'Editing' : 'Viewing'} — {lineCount} line{lineCount !== 1 ? 's' : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
          {!isEditing ? (
            <button type="button" className="file-viewer-btn" onClick={handleEdit} title="Edit file (opens editor)">
              ✏️ Edit
            </button>
          ) : (
            <>
              <button type="button" className="file-viewer-btn file-viewer-btn-save" onClick={handleSave} disabled={saving} title="Save (Ctrl+S)">
                {saving ? '⏳' : '💾'} Save
              </button>
              <button type="button" className="file-viewer-btn" onClick={handleCancel} title="Cancel (Esc)">
                ✕
              </button>
            </>
          )}
          {saveStatus === 'saved' && <span style={{ color: '#4ade80', fontSize: '0.75rem', lineHeight: '28px' }}>✓ Saved</span>}
          {saveStatus === 'error' && <span style={{ color: '#f87171', fontSize: '0.75rem', lineHeight: '28px' }}>✕ Failed</span>}
        </div>
      </div>

      <div className="file-viewer-body">
        {/* Line numbers gutter */}
        <div className="file-viewer-gutter" aria-hidden="true">
          {lines.map((_, i) => (
            <div key={i} className="file-viewer-line-num">{i + 1}</div>
          ))}
        </div>

        {/* Content area */}
        {isEditing ? (
          <textarea
            ref={textareaRef}
            className="file-viewer-textarea"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onKeyDown={handleKeyDown}
            spellCheck={false}
            wrap="off"
          />
        ) : (
          <pre className="file-viewer-pre">{content}</pre>
        )}
      </div>
    </section>
  );
}
