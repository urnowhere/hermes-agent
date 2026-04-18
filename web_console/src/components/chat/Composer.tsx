import { FormEvent, useEffect, useRef, useState } from 'react';
import { apiClient } from '../../lib/api';

interface WorkspaceTreeNode { name: string; path: string; type: 'file' | 'directory'; children?: WorkspaceTreeNode[]; }
interface WorkspaceTreeResponse { ok: boolean; tree: WorkspaceTreeNode; }

function flattenTree(node: WorkspaceTreeNode | undefined): string[] {
  if (!node) return [];
  if (node.type === 'file') return [node.path];
  return (node.children ?? []).flatMap((child) => flattenTree(child));
}

export interface AttachedFile {
  file: File;
  previewUrl: string;
  type: 'image' | 'audio' | 'other';
}

interface ComposerProps {
  onSend(prompt: string, attachments: AttachedFile[], isBackground?: boolean, isQuickAsk?: boolean): Promise<void> | void;
  onNewChat?: () => void;
  reasoningEffort?: string;
  onReasoningChange?: (value: string) => void;
}

interface CommandRegistryEntry {
  name: string;
  description: string;
  aliases?: string[];
  names?: string[];
}

interface CommandsResponse {
  ok: boolean;
  commands: CommandRegistryEntry[];
}

interface SlashCommandOption {
  name: string;
  description: string;
}

const FALLBACK_SLASH_COMMANDS: SlashCommandOption[] = [
  { name: '/new', description: 'Start a new session (fresh session ID + history)' },
  { name: '/reset', description: 'Start a new session (alias for /new)' },
  { name: '/background', description: 'Run a prompt in the background' },
  { name: '/bg', description: 'Run a prompt in the background (alias)' },
  { name: '/btw', description: 'Ephemeral side question (no tools, not persisted)' },
  { name: '/help', description: 'Show available commands' },
];

export function Composer({ onSend, onNewChat, reasoningEffort, onReasoningChange }: ComposerProps) {
  const [prompt, setPrompt] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const [isBackground, setIsBackground] = useState(false);
  const [isQuickAsk, setIsQuickAsk] = useState(false);
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const [slashFilter, setSlashFilter] = useState('');
  const [selectedSlashIndex, setSelectedSlashIndex] = useState(0);
  
  const [showMentionMenu, setShowMentionMenu] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [selectedMentionIndex, setSelectedMentionIndex] = useState(0);
  const [workspaceFiles, setWorkspaceFiles] = useState<string[]>([]);
  const [slashCommands, setSlashCommands] = useState<SlashCommandOption[]>(FALLBACK_SLASH_COMMANDS);
  
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const menuRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let cancelled = false;

    apiClient.get<CommandsResponse>('/commands')
      .then((res) => {
        if (!res.ok || cancelled) return;
        const next = res.commands.flatMap((command) => {
          const names = command.names && command.names.length > 0
            ? command.names
            : [command.name, ...(command.aliases ?? [])];
          return names.map((entryName) => ({
            name: `/${entryName}`,
            description: command.description,
          }));
        });

        if (next.length > 0) {
          const deduped = next.filter((entry, index, arr) => arr.findIndex((other) => other.name === entry.name) === index);
          setSlashCommands(deduped);
        }
      })
      .catch(() => {
        // Fallback list remains in place.
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // Filtered commands for the slash menu
  const filteredCommands = slashFilter
    ? slashCommands.filter(c => c.name.startsWith('/' + slashFilter))
    : slashCommands;

  const filteredMentions = mentionFilter
    ? workspaceFiles.filter(f => f.toLowerCase().includes(mentionFilter.toLowerCase())).slice(0, 50)
    : workspaceFiles.slice(0, 50);

  // Reset selection when filter changes
  useEffect(() => {
    setSelectedSlashIndex(0);
  }, [slashFilter]);

  useEffect(() => {
    setSelectedMentionIndex(0);
  }, [mentionFilter]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setShowSlashMenu(false);
    setShowMentionMenu(false);
    let message = prompt.trim();
    if (!message && attachments.length === 0) return;
    const currentAttachments = [...attachments];
    let bg = isBackground;
    let qa = isQuickAsk;

    // Intercept standalone slash commands
    if (message.startsWith('/btw ') || message === '/btw') {
      qa = true;
      message = message.replace(/^\/btw\s*/, '');
    } else if (message.startsWith('/bg ') || message === '/bg') {
      bg = true;
      message = message.replace(/^\/bg\s*/, '');
    } else if (message.startsWith('/background ') || message === '/background') {
      bg = true;
      message = message.replace(/^\/background\s*/, '');
    }

    setPrompt('');
    setAttachments([]);
    await onSend(message || '(attached files)', currentAttachments, bg, qa);
  };

  const handleFileSelect = () => {
    fileInputRef.current?.click();
  };

  const appendFiles = (filesLike: FileList | File[]) => {
    const files = Array.from(filesLike);
    if (files.length === 0) return;
    const newAttachments: AttachedFile[] = files.map((file) => {
      const type = file.type.startsWith('image/') ? 'image'
        : file.type.startsWith('audio/') ? 'audio'
        : 'other';
      const previewUrl = type === 'image' ? URL.createObjectURL(file) : '';
      return { file, previewUrl, type };
    });
    setAttachments((prev) => [...prev, ...newAttachments]);
  };

  const handleFilesChanged = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    appendFiles(files);
    e.target.value = ''; // Reset input
  };

  const pasteClipboardImage = async () => {
    if (!navigator.clipboard || typeof navigator.clipboard.read !== 'function') {
      return false;
    }
    try {
      const items = await navigator.clipboard.read();
      const pastedFiles: File[] = [];
      for (const item of items) {
        const imageType = item.types.find((type) => type.startsWith('image/'));
        if (!imageType) continue;
        const blob = await item.getType(imageType);
        pastedFiles.push(new File([blob], `clipboard-${Date.now()}.${imageType.split('/')[1] || 'png'}`, { type: imageType }));
      }
      if (pastedFiles.length === 0) return false;
      appendFiles(pastedFiles);
      return true;
    } catch {
      return false;
    }
  };

  useEffect(() => {
    const handleOpenFilePicker = () => {
      handleFileSelect();
    };
    const handlePrefillComposer = (event: Event) => {
      const customEvent = event as CustomEvent<{ value?: string }>;
      const value = customEvent.detail?.value ?? '';
      setPrompt(value);
      handlePromptChange(value);
      requestAnimationFrame(() => textareaRef.current?.focus());
    };
    const handlePasteClipboard = async () => {
      const success = await pasteClipboardImage();
      window.dispatchEvent(new CustomEvent('hermes:composerPasteResult', { detail: { success } }));
    };
    window.addEventListener('hermes:composerOpenFilePicker', handleOpenFilePicker);
    window.addEventListener('hermes:prefillComposer', handlePrefillComposer);
    window.addEventListener('hermes:composerPasteClipboard', handlePasteClipboard);
    return () => {
      window.removeEventListener('hermes:composerOpenFilePicker', handleOpenFilePicker);
      window.removeEventListener('hermes:prefillComposer', handlePrefillComposer);
      window.removeEventListener('hermes:composerPasteClipboard', handlePasteClipboard);
    };
  }, []);

  const removeAttachment = (index: number) => {
    setAttachments((prev) => {
      const removed = prev[index];
      if (removed.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return prev.filter((_, i) => i !== index);
    });
  };

  const toggleRecording = async () => {
    if (isRecording && mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        const file = new File([blob], `recording-${Date.now()}.webm`, { type: 'audio/webm' });
        setAttachments((prev) => [...prev, { file, previewUrl: '', type: 'audio' }]);
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setIsRecording(true);
    } catch {
      // Microphone permission denied
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    
    const files = e.dataTransfer.files;
    if (!files || files.length === 0) return;
    
    const newAttachments: AttachedFile[] = [];
    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const type = file.type.startsWith('image/') ? 'image'
          : file.type.startsWith('audio/') ? 'audio'
          : 'other';
        const previewUrl = type === 'image' ? URL.createObjectURL(file) : '';
        newAttachments.push({ file, previewUrl, type });
    }
    setAttachments((prev) => [...prev, ...newAttachments]);
  };

  const handleSlashSelect = (cmd: string) => {
    setPrompt(cmd + ' ');
    setShowSlashMenu(false);
    setSlashFilter('');
  };

  const handleMentionSelect = (path: string) => {
    const filename = path.split('/').pop() || path;
    const replacement = `[${filename}](${path}) `;
    const newPrompt = prompt.replace(/@\S*$/, replacement);
    setPrompt(newPrompt);
    setShowMentionMenu(false);
    setMentionFilter('');
  };

  const handlePromptChange = (value: string) => {
    setPrompt(value);
    // Show slash menu when typing / at start
    if (value.startsWith('/')) {
      const parts = value.split(' ');
      if (parts.length === 1) {
        // Still typing the command name
        setSlashFilter(value.slice(1));
        setShowSlashMenu(true);
      } else {
        setShowSlashMenu(false);
      }
    } else {
      setShowSlashMenu(false);
    }
    
    // Check for @mentions
    const atMatch = value.match(/(?:^|\s)@(\S*)$/);
    if (atMatch) {
      setMentionFilter(atMatch[1]);
      setShowMentionMenu(true);
      if (workspaceFiles.length === 0) {
        apiClient.get<WorkspaceTreeResponse>('/workspace/tree').then(res => {
          if (res.ok) setWorkspaceFiles(flattenTree(res.tree));
        }).catch(() => {});
      }
    } else {
      setShowMentionMenu(false);
    }
  };

  return (
    <div style={{ width: '100%', padding: '0 20px 20px' }}>
      <form 
        className="composer" 
        aria-label="Composer" 
        onSubmit={handleSubmit} 
        onDragOver={handleDragOver}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        style={{ 
          margin: '0 auto', 
          maxWidth: '800px', 
          position: 'relative', 
          display: 'flex', 
          flexDirection: 'column',
          boxShadow: isDragOver ? '0 0 0 2px #c084fc' : 'none',
          borderRadius: '24px',
          transition: 'box-shadow 0.2s ease',
        }}
      >
        {isDragOver && (
          <div style={{
            position: 'absolute',
            inset: 0,
            background: 'rgba(192, 132, 252, 0.1)',
            backdropFilter: 'blur(4px)',
            borderRadius: '24px',
            zIndex: 50,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#c084fc',
            fontSize: '1.2rem',
            fontWeight: 600,
            pointerEvents: 'none'
          }}>
            Drop files here...
          </div>
        )}
        
        {/* Attachment Preview Strip */}
        {attachments.length > 0 && (
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', paddingBottom: '12px', paddingLeft: '16px' }}>
            {attachments.map((att, i) => (
              <div key={i} style={{
                position: 'relative',
                background: 'rgba(255,255,255,0.08)',
                borderRadius: '12px',
                overflow: 'hidden',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: att.type === 'image' ? '0' : '8px 12px',
                maxWidth: '200px',
                backdropFilter: 'blur(10px)'
              }}>
                {att.type === 'image' ? (
                  <img src={att.previewUrl} alt="preview" style={{ height: '64px', width: '64px', objectFit: 'cover', borderRadius: '12px' }} />
                ) : (
                  <span style={{ color: '#a5b4fc', fontSize: '0.85rem' }}>
                    {att.type === 'audio' ? '🎵' : '📄'} {att.file.name.slice(0, 20)}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => removeAttachment(i)}
                  style={{
                    position: 'absolute', top: '2px', right: '2px',
                    background: 'rgba(0,0,0,0.6)', color: 'white',
                    border: 'none', borderRadius: '50%',
                    width: '20px', height: '20px',
                    cursor: 'pointer', fontSize: '12px',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >×</button>
              </div>
            ))}
          </div>
        )}

        {/* Slash command autocomplete menu */}
        {showSlashMenu && filteredCommands.length > 0 && (
          <div
            ref={menuRef}
            style={{
              position: 'absolute',
              bottom: '100%',
              left: 0,
              right: 0,
              maxHeight: '280px',
              overflowY: 'auto',
              background: 'rgba(24, 24, 27, 0.98)',
              border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: '12px',
              padding: '6px',
              marginBottom: '4px',
              backdropFilter: 'blur(16px)',
              boxShadow: '0 -8px 32px rgba(0,0,0,0.4)',
              zIndex: 100,
            }}
          >
            <div style={{ padding: '4px 10px 8px', fontSize: '0.7rem', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Slash Commands
            </div>
            {filteredCommands.map((cmd, i) => (
              <button
                key={cmd.name}
                type="button"
                onClick={() => handleSlashSelect(cmd.name)}
                onMouseEnter={() => setSelectedSlashIndex(i)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  width: '100%',
                  padding: '8px 10px',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  background: i === selectedSlashIndex ? 'rgba(129, 140, 248, 0.15)' : 'transparent',
                  color: '#f4f4f5',
                  textAlign: 'left',
                  transition: 'background 0.1s',
                }}
              >
                <span style={{
                  fontFamily: 'monospace',
                  fontWeight: 600,
                  color: '#818cf8',
                  fontSize: '0.85rem',
                  minWidth: '120px',
                }}>{cmd.name}</span>
                <span style={{
                  color: '#71717a',
                  fontSize: '0.8rem',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>{cmd.description}</span>
              </button>
            ))}
          </div>
        )}

        {/* Mention autocomplete menu */}
        {showMentionMenu && filteredMentions.length > 0 && (
          <div
            style={{
              position: 'absolute',
              bottom: '100%',
              left: '40px',
              maxWidth: '400px',
              maxHeight: '280px',
              overflowY: 'auto',
              background: 'rgba(24, 24, 27, 0.98)',
              border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: '12px',
              padding: '6px',
              marginBottom: '4px',
              backdropFilter: 'blur(16px)',
              boxShadow: '0 -8px 32px rgba(0,0,0,0.4)',
              zIndex: 101,
            }}
          >
            <div style={{ padding: '4px 10px 8px', fontSize: '0.7rem', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              File Mentions
            </div>
            {filteredMentions.map((path, i) => (
              <button
                key={path}
                type="button"
                onClick={() => handleMentionSelect(path)}
                onMouseEnter={() => setSelectedMentionIndex(i)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '2px',
                  width: '100%',
                  padding: '8px 10px',
                  border: 'none',
                  borderRadius: '8px',
                  cursor: 'pointer',
                  background: i === selectedMentionIndex ? 'rgba(129, 140, 248, 0.15)' : 'transparent',
                  textAlign: 'left',
                  transition: 'background 0.1s',
                }}
              >
                <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#f4f4f5' }}>
                  {path.split('/').pop()}
                </span>
                <span style={{ fontSize: '0.75rem', color: '#64748b', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', width: '100%' }}>
                  {path}
                </span>
              </button>
            ))}
          </div>
        )}

        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '24px', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '8px', backdropFilter: 'blur(8px)' }}>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*,audio/*,.pdf,.txt,.md,.json,.csv"
            multiple
            style={{ display: 'none' }}
            onChange={handleFilesChanged}
          />

          <textarea
            ref={textareaRef}
            id="chat-prompt"
            className="composer-textarea"
            style={{ margin: 0, minHeight: '44px', maxHeight: '200px', border: 'none', background: 'transparent', boxShadow: 'none', padding: '4px 8px', fontSize: '1.05rem', resize: 'none', outline: 'none', color: '#f4f4f5' }}
            placeholder={isQuickAsk ? "Ask a quick question without polluting conversation memory..." : (isBackground ? "Run silently in background..." : "Message Hermes... (type / for commands)")}
            rows={1}
            value={prompt}
            onChange={(event) => {
              handlePromptChange(event.target.value);
              // Auto-resize
              event.target.style.height = 'auto';
              event.target.style.height = `${Math.min(event.target.scrollHeight, 200)}px`;
            }}
            onKeyDown={(e) => {
              if (showSlashMenu && filteredCommands.length > 0) {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setSelectedSlashIndex(prev => Math.min(prev + 1, filteredCommands.length - 1));
                  return;
                }
                if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setSelectedSlashIndex(prev => Math.max(prev - 1, 0));
                  return;
                }
                if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                  e.preventDefault();
                  handleSlashSelect(filteredCommands[selectedSlashIndex].name);
                  return;
                }
                if (e.key === 'Escape') {
                  e.preventDefault();
                  setShowSlashMenu(false);
                  return;
                }
              }
              if (showMentionMenu && filteredMentions.length > 0) {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setSelectedMentionIndex(prev => Math.min(prev + 1, filteredMentions.length - 1));
                  return;
                }
                if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setSelectedMentionIndex(prev => Math.max(prev - 1, 0));
                  return;
                }
                if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
                  e.preventDefault();
                  handleMentionSelect(filteredMentions[selectedMentionIndex]);
                  return;
                }
                if (e.key === 'Escape') {
                  e.preventDefault();
                  setShowMentionMenu(false);
                  return;
                }
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
          />

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 4px' }}>
            <div className="composer-toolbar" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
              <button type="button" className="icon-button" title="Attach file" onClick={handleFileSelect}>📎</button>
              <button
                type="button"
                className="icon-button"
                title={isRecording ? 'Stop recording' : 'Voice input'}
                onClick={toggleRecording}
                style={isRecording ? { background: 'rgba(239, 68, 68, 0.3)', color: '#f87171', animation: 'pulse 1.5s infinite' } : {}}
              >
                {isRecording ? '⏹' : '🎤'}
              </button>
              <button type="button" className="icon-button" title="New chat" onClick={onNewChat}>✨</button>
              
              <div 
                style={{ 
                  display: 'flex', 
                  alignItems: 'center', 
                  gap: '6px', 
                  marginLeft: '8px',
                  background: isBackground ? 'rgba(74, 222, 128, 0.1)' : 'transparent',
                  border: `1px solid ${isBackground ? 'rgba(74, 222, 128, 0.3)' : 'transparent'}`,
                  padding: '4px 8px',
                  borderRadius: '12px',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  userSelect: 'none'
                }}
                onClick={() => { setIsBackground(!isBackground); setIsQuickAsk(false); }}
              >
                <input 
                  type="checkbox" 
                  checked={isBackground} 
                  onChange={() => {}} 
                  style={{ cursor: 'pointer', accentColor: '#4ade80', width: '14px', height: '14px' }} 
                />
                <span style={{ fontSize: '0.8rem', color: isBackground ? '#4ade80' : '#a1a1aa', fontWeight: isBackground ? 600 : 400 }}>
                  Background
                </span>
              </div>
              
              <div 
                style={{ 
                  display: 'flex', 
                  alignItems: 'center', 
                  gap: '6px', 
                  background: isQuickAsk ? 'rgba(192, 132, 252, 0.1)' : 'transparent',
                  border: `1px solid ${isQuickAsk ? 'rgba(192, 132, 252, 0.3)' : 'transparent'}`,
                  padding: '4px 8px',
                  borderRadius: '12px',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  userSelect: 'none'
                }}
                onClick={() => { setIsQuickAsk(!isQuickAsk); setIsBackground(false); }}
              >
                <input 
                  type="checkbox" 
                  checked={isQuickAsk} 
                  onChange={() => {}} 
                  style={{ cursor: 'pointer', accentColor: '#c084fc', width: '14px', height: '14px' }} 
                />
                <span style={{ fontSize: '0.8rem', color: isQuickAsk ? '#c084fc' : '#a1a1aa', fontWeight: isQuickAsk ? 600 : 400 }}>
                  Quick Ask
                </span>
              </div>
              
              {reasoningEffort !== undefined && onReasoningChange && (
                <select
                  value={reasoningEffort}
                  onChange={(e) => onReasoningChange(e.target.value)}
                  title="Reasoning Effort (Supported by O-series / Copilot / etc)"
                  style={{
                    marginLeft: '8px',
                    padding: '4px 8px',
                    borderRadius: '12px',
                    background: 'transparent',
                    border: '1px solid rgba(255,255,255,0.1)',
                    color: '#a1a1aa',
                    fontSize: '0.8rem',
                    cursor: 'pointer',
                    outline: 'none'
                  }}
                >
                  <option value="none">Reasoning: None</option>
                  <option value="minimal">Reasoning: Minimal</option>
                  <option value="low">Reasoning: Low</option>
                  <option value="medium">Reasoning: Medium</option>
                  <option value="high">Reasoning: High</option>
                  <option value="xhigh">Reasoning: XHigh</option>
                </select>
              )}
            </div>
            <div className="composer-actions">
              <button type="submit" disabled={!prompt.trim() && attachments.length === 0} style={{ 
                padding: '8px 20px', 
                borderRadius: '99px', 
                background: prompt.trim() || attachments.length > 0 ? (isQuickAsk ? '#c084fc' : (isBackground ? '#4ade80' : '#f4f4f5')) : 'rgba(255,255,255,0.1)', 
                color: prompt.trim() || attachments.length > 0 ? '#18181b' : 'rgba(255,255,255,0.4)',
                border: 'none',
                fontWeight: 500,
                cursor: prompt.trim() || attachments.length > 0 ? 'pointer' : 'not-allowed',
                transition: 'all 0.2s ease'
              }}>
                {isQuickAsk ? 'Ask' : (isBackground ? 'Dispatch' : 'Send')}
              </button>
            </div>
          </div>
        </div>
      </form>
    </div>
  );
}
