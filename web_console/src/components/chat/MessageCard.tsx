import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { apiClient } from '../../lib/api';
import { getBackendUrl } from '../../store/backendStore';

interface MessageCardProps {
  role: 'user' | 'assistant' | 'tool' | 'system';
  title: string;
  content: string;
  isStreaming?: boolean;
  reasoning?: string;
  isReasoningStreaming?: boolean;
  messageIndex?: number;
  onBranch?: (messageIndex: number) => void;
}

interface TtsResponse {
  ok: boolean;
  tts?: { success?: boolean; audio_file?: string; error?: string };
}

export function MessageCard({ role, title, content, isStreaming, reasoning, isReasoningStreaming, messageIndex, onBranch }: MessageCardProps) {
  const [ttsLoading, setTtsLoading] = useState(false);
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const [showActions, setShowActions] = useState(false);
  const avatar = role === 'user' ? '👤' : role === 'assistant' ? '🤖' : role === 'tool' ? '⚙️' : '⚡';

  const handleTts = async () => {
    if (ttsLoading || !content.trim()) return;
    setTtsLoading(true);
    try {
      const res = await apiClient.post<TtsResponse>('/media/tts', { text: content });
      if (res.ok && res.tts?.audio_file) {
        // Fetch the audio file as a blob since the backend returns a filesystem path
        try {
          const audioRes = await fetch(`${getBackendUrl()}/api/gui/workspace/file?path=${encodeURIComponent(res.tts.audio_file)}`);
          if (audioRes.ok) {
            const blob = await audioRes.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio.onended = () => URL.revokeObjectURL(url);
            audio.play().catch(() => {});
          }
        } catch {
          // Try direct path as fallback
          const audio = new Audio(res.tts.audio_file);
          audio.play().catch(() => {});
        }
      }
    } catch {
      // TTS not available
    } finally {
      setTtsLoading(false);
    }
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text).catch(() => {});
  };

  const handleBranch = () => {
    if (onBranch && messageIndex !== undefined) {
      onBranch(messageIndex);
    }
  };

  const hasReasoning = !!(reasoning && reasoning.trim());

  return (
    <article
      className={`message-card message-card-${role}`}
      style={{ display: 'flex', gap: '16px', alignItems: 'flex-start', position: 'relative' }}
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      <div style={{ fontSize: '1.5rem', flexShrink: 0, opacity: 0.9 }}>{avatar}</div>
      <div style={{ flex: 1, minWidth: 0, overflow: 'hidden' }}>
        <div className="message-card-title" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {title}
          {role === 'assistant' && content.length > 0 && (
            <button
              type="button"
              onClick={handleTts}
              disabled={ttsLoading}
              title="Read aloud (TTS)"
              style={{
                background: 'rgba(255,255,255,0.06)',
                border: '1px solid rgba(255,255,255,0.1)',
                color: ttsLoading ? '#475569' : '#a5b4fc',
                padding: '2px 8px',
                borderRadius: '6px',
                cursor: ttsLoading ? 'default' : 'pointer',
                fontSize: '0.75rem',
                transition: 'all 0.2s',
              }}
            >
              {ttsLoading ? '⏳' : '🔊'}
            </button>
          )}
        </div>

        {/* Collapsible Reasoning/Thinking Block */}
        {(hasReasoning || isReasoningStreaming) && (
          <div style={{
            margin: '8px 0',
            borderRadius: '10px',
            background: 'rgba(251, 191, 36, 0.04)',
            border: '1px solid rgba(251, 191, 36, 0.12)',
            overflow: 'hidden',
            transition: 'all 0.2s ease',
          }}>
            <button
              type="button"
              onClick={() => setReasoningOpen(!reasoningOpen)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                width: '100%',
                padding: '8px 12px',
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: '#fbbf24',
                fontSize: '0.8rem',
                fontWeight: 500,
                textAlign: 'left',
                transition: 'background 0.15s',
              }}
            >
              <span style={{
                display: 'inline-flex',
                transition: 'transform 0.2s ease',
                transform: reasoningOpen ? 'rotate(90deg)' : 'rotate(0deg)',
                fontSize: '0.7rem',
              }}>▶</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                💭 Thinking
                {isReasoningStreaming && (
                  <span style={{
                    display: 'inline-block',
                    width: '6px', height: '6px',
                    borderRadius: '50%',
                    background: '#fbbf24',
                    animation: 'pulse 1.5s infinite',
                  }} />
                )}
              </span>
              {reasoning && (
                <span style={{
                  marginLeft: 'auto',
                  fontSize: '0.7rem',
                  color: '#92400e',
                  opacity: 0.6,
                  fontFamily: "'JetBrains Mono', monospace",
                }}>
                  {reasoning.length.toLocaleString()} chars
                </span>
              )}
            </button>
            {reasoningOpen && (
              <div style={{
                padding: '0 12px 10px',
                fontSize: '0.85rem',
                lineHeight: 1.5,
                color: '#d4a574',
                fontStyle: 'italic',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                maxHeight: '400px',
                overflowY: 'auto',
                borderTop: '1px solid rgba(251, 191, 36, 0.08)',
              }}>
                {reasoning}
                {isReasoningStreaming && (
                  <span style={{
                    display: 'inline-block',
                    width: '2px', height: '1em',
                    background: '#fbbf24',
                    marginLeft: '2px',
                    verticalAlign: 'text-bottom',
                    animation: 'blink 1s step-end infinite',
                  }} />
                )}
              </div>
            )}
          </div>
        )}

        <div className="message-card-content markdown-body" style={{ wordBreak: 'break-word', overflowX: 'auto' }}>
          {role === 'assistant' || role === 'user' ? (
            <>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code(props) {
                  const { children, className, node, ...rest } = props;
                  const match = /language-(\w+)/.exec(className || '');
                  const isInline = !match && !String(children).includes('\n');
                  const language = match ? match[1] : '';
                  const contentString = String(children).replace(/\n$/, '');

                  if (!isInline && match) {
                    return (
                      <div className="code-block-wrapper" style={{ position: 'relative', marginTop: '12px', marginBottom: '12px' }}>
                        <div style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          background: '#1e1e1e',
                          padding: '4px 12px',
                          borderTopLeftRadius: '6px',
                          borderTopRightRadius: '6px',
                          fontSize: '0.75rem',
                          color: '#9cdcfe',
                          borderBottom: '1px solid #333'
                        }}>
                          <span>{language}</span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <button
                              onClick={() => handleCopy(contentString)}
                              style={{
                                background: 'transparent',
                                border: 'none',
                                color: '#d4d4d4',
                                cursor: 'pointer',
                                padding: '2px 6px',
                                fontSize: '0.75rem',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px'
                              }}
                              title="Copy to clipboard"
                            >
                              📋 Copy
                            </button>
                            {['html', 'svg', 'xml'].includes(language.toLowerCase()) && (
                              <button
                                onClick={() => {
                                  window.dispatchEvent(new CustomEvent('hermes:openArtifact', {
                                    detail: { type: language.toLowerCase(), content: contentString }
                                  }));
                                }}
                                style={{
                                  background: 'rgba(129, 140, 248, 0.2)',
                                  border: '1px solid rgba(129, 140, 248, 0.4)',
                                  color: '#a5b4fc',
                                  cursor: 'pointer',
                                  padding: '2px 8px',
                                  borderRadius: '4px',
                                  fontSize: '0.75rem',
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: '4px'
                                }}
                                title="Open in Canvas Overlay"
                              >
                                🎨 Open Canvas
                              </button>
                            )}
                          </div>
                        </div>
                        <SyntaxHighlighter
                          style={vscDarkPlus as any}
                          language={language}
                          PreTag="div"
                          customStyle={{ margin: 0, borderTopLeftRadius: 0, borderTopRightRadius: 0, fontSize: '0.85rem' }}
                          {...rest as any}
                        >
                          {contentString}
                        </SyntaxHighlighter>
                      </div>
                    );
                  }
                  
                  return (
                    <code
                      style={{
                        background: 'rgba(255, 255, 255, 0.1)',
                        padding: '2px 4px',
                        borderRadius: '4px',
                        fontFamily: 'monospace',
                        fontSize: '0.85em',
                      }}
                      className={className}
                      {...rest as any}
                    >
                      {children}
                    </code>
                  );
                },
                a: ({ node, ...props }) => <a style={{ color: '#818cf8', textDecoration: 'underline' }} target="_blank" rel="noopener noreferrer" {...props as any} />,
                p: ({ node, ...props }) => <p style={{ margin: '0 0 8px 0', lineHeight: 1.6 }} {...props as any} />,
                ul: ({ node, ...props }) => <ul style={{ paddingLeft: '20px', margin: '0 0 12px 0' }} {...props as any} />,
                ol: ({ node, ...props }) => <ol style={{ paddingLeft: '20px', margin: '0 0 12px 0' }} {...props as any} />,
                li: ({ node, ...props }) => <li style={{ margin: '4px 0' }} {...props as any} />,
                table: ({ node, ...props }) => (
                  <div style={{ overflowX: 'auto', marginBottom: '12px' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }} {...props as any} />
                  </div>
                ),
                th: ({ node, ...props }) => <th style={{ border: '1px solid rgba(255,255,255,0.1)', padding: '6px 12px', background: 'rgba(255,255,255,0.05)', textAlign: 'left' }} {...props as any} />,
                td: ({ node, ...props }) => <td style={{ border: '1px solid rgba(255,255,255,0.1)', padding: '6px 12px' }} {...props as any} />
              }}
            >
              {content || ' '}
            </ReactMarkdown>
            {isStreaming && (
              <span className="streaming-cursor" style={{
                display: 'inline-block',
                width: '2px',
                height: '1.1em',
                background: '#818cf8',
                marginLeft: '2px',
                verticalAlign: 'text-bottom',
                animation: 'blink 1s step-end infinite',
              }} />
            )}
            </>
          ) : (
            <div style={{ whiteSpace: 'pre-wrap', fontFamily: role === 'tool' ? 'monospace' : 'inherit', fontSize: role === 'tool' ? '0.85rem' : 'inherit' }}>
              {content}
            </div>
          )}
        </div>
      </div>

      {/* Hover action buttons */}
      {showActions && (role === 'assistant' || role === 'user') && (
        <div style={{
          position: 'absolute',
          top: '4px',
          right: '4px',
          display: 'flex',
          gap: '4px',
          opacity: 0.8,
        }}>
          {role === 'assistant' && onBranch && messageIndex !== undefined && (
            <button
              type="button"
              onClick={handleBranch}
              title="Branch conversation from here"
              style={{
                background: 'rgba(99, 102, 241, 0.15)',
                border: '1px solid rgba(99, 102, 241, 0.25)',
                color: '#a5b4fc',
                padding: '3px 8px',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '0.7rem',
                fontWeight: 500,
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                transition: 'all 0.15s',
              }}
            >
              🌿 Branch
            </button>
          )}
          <button
            type="button"
            onClick={() => handleCopy(content)}
            title="Copy message"
            style={{
              background: 'rgba(255,255,255,0.06)',
              border: '1px solid rgba(255,255,255,0.1)',
              color: '#94a3b8',
              padding: '3px 8px',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '0.7rem',
              transition: 'all 0.15s',
            }}
          >
            📋
          </button>
        </div>
      )}
    </article>
  );
}
