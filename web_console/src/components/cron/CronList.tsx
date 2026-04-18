import { useState } from 'react';
import { apiClient } from '../../lib/api';

interface CronJob {
  id: string;
  name: string;
  schedule: string;
  status: string;
  prompt?: string;
  deliver?: string;
}

interface CronListProps {
  jobs: CronJob[];
  onPause?: (id: string) => Promise<void>;
  onResume?: (id: string) => Promise<void>;
  onRun?: (id: string) => Promise<void>;
  onDelete?: (id: string) => Promise<void>;
  onCreate?: (data: { name: string; schedule: string; prompt: string }) => Promise<void>;
  onUpdate?: (id: string, data: Partial<{ name: string; schedule: string; prompt: string; deliver: string }>) => Promise<void>;
}

interface HistoryEntry {
  run_id?: string;
  status?: string;
  started_at?: string;
  duration_s?: number;
  output_preview?: string;
}

interface HistoryResponse {
  ok: boolean;
  history?: HistoryEntry[];
}

const STATUS_STYLES: Record<string, { bg: string; color: string; border: string; label: string }> = {
  active: { bg: 'rgba(34, 197, 94, 0.15)', color: '#86efac', border: 'rgba(34, 197, 94, 0.3)', label: '● Active' },
  paused: { bg: 'rgba(251, 191, 36, 0.15)', color: '#fde68a', border: 'rgba(251, 191, 36, 0.3)', label: '⏸ Paused' },
  disabled: { bg: 'rgba(100, 116, 139, 0.15)', color: '#94a3b8', border: 'rgba(100, 116, 139, 0.3)', label: '○ Disabled' },
};

const DELIVERY_OPTIONS = [
  { value: '', label: 'Current session' },
  { value: 'telegram', label: 'Telegram' },
  { value: 'discord', label: 'Discord' },
  { value: 'slack', label: 'Slack' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'matrix', label: 'Matrix' },
];

export function CronList({ jobs, onPause, onResume, onRun, onDelete, onCreate, onUpdate }: CronListProps) {
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newSchedule, setNewSchedule] = useState('');
  const [newPrompt, setNewPrompt] = useState('');
  const [expandedHistory, setExpandedHistory] = useState<string | null>(null);
  const [historyEntries, setHistoryEntries] = useState<HistoryEntry[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editFields, setEditFields] = useState<{ name: string; schedule: string; prompt: string; deliver: string }>({ name: '', schedule: '', prompt: '', deliver: '' });

  const handleCreate = async () => {
    if (!newName.trim() || !newSchedule.trim() || !newPrompt.trim() || !onCreate) return;
    await onCreate({ name: newName.trim(), schedule: newSchedule.trim(), prompt: newPrompt.trim() });
    setNewName('');
    setNewSchedule('');
    setNewPrompt('');
    setShowCreate(false);
  };

  const startEdit = (job: CronJob) => {
    setEditingId(job.id);
    setEditFields({
      name: job.name,
      schedule: job.schedule,
      prompt: job.prompt || '',
      deliver: job.deliver || '',
    });
  };

  const saveEdit = async () => {
    if (!editingId || !onUpdate) return;
    await onUpdate(editingId, {
      name: editFields.name.trim(),
      schedule: editFields.schedule.trim(),
      prompt: editFields.prompt.trim(),
      deliver: editFields.deliver || undefined,
    });
    setEditingId(null);
  };

  const cancelEdit = () => setEditingId(null);

  const btnStyle: React.CSSProperties = {
    background: 'rgba(255, 255, 255, 0.06)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#a5b4fc',
    padding: '6px 12px',
    borderRadius: '8px',
    cursor: 'pointer',
    fontSize: '0.8rem',
    transition: 'all 0.2s',
  };

  const inputStyle: React.CSSProperties = {
    padding: '10px 14px',
    borderRadius: '10px',
    border: '1px solid rgba(129, 140, 248, 0.3)',
    background: 'rgba(0, 0, 0, 0.2)',
    color: 'white',
    fontSize: '0.9rem',
    width: '100%',
  };

  return (
    <section style={{
      background: 'rgba(255, 255, 255, 0.03)',
      border: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: '16px',
      padding: '20px',
    }} aria-label="Automations list">
      <div style={{ marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>⏰ Automations</h2>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>Manage cron jobs and scheduled Hermes workflows.</p>
        </div>
        {onCreate && (
          <button
            type="button"
            onClick={() => setShowCreate(!showCreate)}
            style={{ ...btnStyle, background: 'rgba(129, 140, 248, 0.15)', borderColor: 'rgba(129, 140, 248, 0.3)' }}
          >
            {showCreate ? '✕ Cancel' : '+ New Job'}
          </button>
        )}
      </div>

      {/* Create form */}
      {showCreate && (
        <div style={{
          display: 'flex', flexDirection: 'column', gap: '10px',
          marginBottom: '16px', padding: '16px',
          background: 'rgba(129, 140, 248, 0.06)',
          border: '1px solid rgba(129, 140, 248, 0.15)',
          borderRadius: '14px',
        }}>
          <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Job name (e.g. Morning summary)" style={inputStyle} />
          <input type="text" value={newSchedule} onChange={(e) => setNewSchedule(e.target.value)} placeholder="Cron schedule (e.g. 0 9 * * *)" style={inputStyle} />
          <textarea value={newPrompt} onChange={(e) => setNewPrompt(e.target.value)} placeholder="Prompt to execute..." rows={3} style={{ ...inputStyle, resize: 'vertical' }} />
          <button type="button" onClick={handleCreate} style={{ ...btnStyle, background: 'rgba(34, 197, 94, 0.2)', color: '#86efac', borderColor: 'rgba(34, 197, 94, 0.3)', padding: '10px 20px', alignSelf: 'flex-end' }}>
            Create Job
          </button>
        </div>
      )}

      {/* Jobs list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {jobs.map((job) => {
          const style = STATUS_STYLES[job.status] || STATUS_STYLES.disabled;
          const isEditing = editingId === job.id;
          const isExpanded = expandedHistory === job.id;

          return (
            <div key={job.id}>
              {/* Job card */}
              <div style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '14px 16px',
                background: style.bg,
                border: `1px solid ${style.border}`,
                borderRadius: (isExpanded || isEditing) ? '14px 14px 0 0' : '14px',
                transition: 'all 0.2s',
              }}>
                <div style={{ flex: 1 }}>
                  <strong style={{ color: '#e2e8f0', fontSize: '0.95rem' }}>{job.name}</strong>
                  <div style={{ display: 'flex', gap: '12px', marginTop: '4px', fontSize: '0.8rem', flexWrap: 'wrap' }}>
                    <code style={{ color: '#a5b4fc', background: 'rgba(0,0,0,0.2)', padding: '2px 6px', borderRadius: '4px' }}>{job.schedule}</code>
                    <span style={{ color: style.color }}>{style.label}</span>
                    {job.deliver && <span style={{ color: '#94a3b8' }}>→ {job.deliver}</span>}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
                  {onRun && <button type="button" onClick={() => onRun(job.id)} style={btnStyle} title="Run now">▶️</button>}
                  {onUpdate && (
                    <button type="button" onClick={() => isEditing ? cancelEdit() : startEdit(job)} style={{ ...btnStyle, color: isEditing ? '#fca5a5' : '#a5b4fc' }} title="Edit">
                      {isEditing ? '✕' : '✏️'}
                    </button>
                  )}
                  {job.status === 'active' && onPause && (
                    <button type="button" onClick={() => onPause(job.id)} style={btnStyle} title="Pause">⏸</button>
                  )}
                  {job.status === 'paused' && onResume && (
                    <button type="button" onClick={() => onResume(job.id)} style={btnStyle} title="Resume">▶</button>
                  )}
                  {onDelete && (
                    <button type="button" onClick={() => onDelete(job.id)} style={{ ...btnStyle, color: '#fca5a5' }} title="Delete">🗑️</button>
                  )}
                  <button type="button" onClick={async () => {
                    if (isExpanded) {
                      setExpandedHistory(null);
                    } else {
                      try {
                        const res = await apiClient.get<HistoryResponse>(`/cron/jobs/${job.id}/history`);
                        setHistoryEntries(res.ok && res.history ? res.history : []);
                      } catch { setHistoryEntries([]); }
                      setExpandedHistory(job.id);
                    }
                  }} style={btnStyle} title="Run history">📜</button>
                </div>
              </div>

              {/* Edit panel */}
              {isEditing && (
                <div style={{
                  padding: '14px 16px',
                  background: 'rgba(129, 140, 248, 0.06)',
                  border: `1px solid ${style.border}`,
                  borderTop: 'none',
                  borderRadius: isExpanded ? '0' : '0 0 14px 14px',
                }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '10px' }}>
                    <label style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <span style={{ color: '#94a3b8', fontSize: '0.75rem' }}>Name</span>
                      <input
                        type="text"
                        value={editFields.name}
                        onChange={(e) => setEditFields(f => ({ ...f, name: e.target.value }))}
                        style={inputStyle}
                      />
                    </label>
                    <label style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <span style={{ color: '#94a3b8', fontSize: '0.75rem' }}>Schedule</span>
                      <input
                        type="text"
                        value={editFields.schedule}
                        onChange={(e) => setEditFields(f => ({ ...f, schedule: e.target.value }))}
                        style={inputStyle}
                      />
                    </label>
                  </div>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginBottom: '10px' }}>
                    <span style={{ color: '#94a3b8', fontSize: '0.75rem' }}>Prompt</span>
                    <textarea
                      value={editFields.prompt}
                      onChange={(e) => setEditFields(f => ({ ...f, prompt: e.target.value }))}
                      rows={3}
                      style={{ ...inputStyle, resize: 'vertical' }}
                    />
                  </label>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ color: '#94a3b8', fontSize: '0.75rem' }}>Deliver to</span>
                      <select
                        value={editFields.deliver}
                        onChange={(e) => setEditFields(f => ({ ...f, deliver: e.target.value }))}
                        style={{ ...inputStyle, width: 'auto', padding: '6px 10px' }}
                      >
                        {DELIVERY_OPTIONS.map(o => (
                          <option key={o.value} value={o.value} style={{ background: '#0f172a' }}>{o.label}</option>
                        ))}
                      </select>
                    </label>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button type="button" onClick={cancelEdit} style={btnStyle}>Cancel</button>
                      <button type="button" onClick={saveEdit} style={{ ...btnStyle, background: 'rgba(34, 197, 94, 0.2)', color: '#86efac', borderColor: 'rgba(34, 197, 94, 0.3)' }}>
                        Save Changes
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* History expansion */}
              {isExpanded && (
                <div style={{
                  padding: '10px 16px',
                  background: 'rgba(0,0,0,0.15)', borderRadius: '0 0 14px 14px',
                  border: `1px solid ${style.border}`, borderTop: 'none',
                  fontSize: '0.8rem', color: '#94a3b8',
                }}>
                  {historyEntries.length === 0 ? (
                    <span>No run history yet.</span>
                  ) : (
                    historyEntries.slice(0, 10).map((h, i) => (
                      <div key={h.run_id ?? i} style={{ display: 'flex', gap: '12px', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.04)', alignItems: 'center' }}>
                        <span style={{
                          color: h.status === 'success' ? '#86efac' : h.status === 'failed' ? '#fca5a5' : '#fde68a',
                          fontSize: '0.75rem',
                          padding: '1px 6px',
                          borderRadius: '4px',
                          background: h.status === 'success' ? 'rgba(34,197,94,0.1)' : h.status === 'failed' ? 'rgba(239,68,68,0.1)' : 'rgba(251,191,36,0.1)',
                        }}>
                          {h.status ?? 'unknown'}
                        </span>
                        {h.started_at && <span>{new Date(h.started_at).toLocaleString()}</span>}
                        {h.duration_s != null && <span style={{ color: '#64748b' }}>{h.duration_s.toFixed(1)}s</span>}
                        {h.output_preview && (
                          <span style={{ color: '#475569', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {h.output_preview}
                          </span>
                        )}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          );
        })}
        {jobs.length === 0 && (
          <p style={{ color: '#475569', textAlign: 'center', padding: '24px', fontSize: '0.9rem' }}>
            No automations configured. Create one to schedule recurring agent tasks.
          </p>
        )}
      </div>
    </section>
  );
}
