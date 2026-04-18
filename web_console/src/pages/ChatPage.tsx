import { useEffect, useRef, useState } from 'react';
import { apiClient } from '../lib/api';
import { getBackendUrl } from '../store/backendStore';
import { openSessionEventStream, type GuiEvent } from '../lib/events';
import { ApprovalPrompt } from '../components/chat/ApprovalPrompt';
import { ClarifyPrompt } from '../components/chat/ClarifyPrompt';
import { Composer, type AttachedFile } from '../components/chat/Composer';
import { RunStatusBar } from '../components/chat/RunStatusBar';
import { ToolTimeline } from '../components/chat/ToolTimeline';
import { Transcript, type TranscriptItem } from '../components/chat/Transcript';
import { UsageBar } from '../components/chat/UsageBar';
import { SessionSidebar } from '../components/chat/SessionSidebar';

interface HumanRequest {
  id: string;
  request_id?: string;
  kind: 'approval' | 'clarify';
  command?: string;
  message?: string;
  prompt?: string;
  title?: string;
  expires_at?: number;
}

interface HumanPendingResponse {
  ok: boolean;
  pending: HumanRequest[];
}

interface ChatSendResponse {
  ok: boolean;
  session_id: string;
  run_id: string;
  status: string;
}

interface CommandRegistryEntry {
  name: string;
  description: string;
  category: string;
  aliases?: string[];
}

interface CommandsResponse {
  ok: boolean;
  commands: CommandRegistryEntry[];
}

interface SettingsResponse {
  ok: boolean;
  settings?: Record<string, any>;
}

interface YoloResponse {
  ok: boolean;
  session_id: string;
  enabled: boolean;
}

interface SessionsListResponse {
  ok: boolean;
  sessions: Array<{ session_id: string; title?: string | null; source?: string }>;
}

interface SessionSearchResponse {
  ok: boolean;
  search?: {
    results?: Array<{ session_id: string; snippet?: string; session_title?: string }>;
  };
}

interface TranscriptResponse {
  ok: boolean;
  items: Array<{ type?: string; role?: string; content?: string }>;
}

interface GatewayPlatformsResponse {
  ok: boolean;
  platforms: Array<{
    key: string;
    label: string;
    enabled?: boolean;
    configured?: boolean;
    runtime_state?: string;
    error_message?: string | null;
  }>;
}

interface GatewayOverviewResponse {
  ok: boolean;
  overview?: {
    summary?: {
      platform_count?: number;
      enabled_platforms?: number;
      connected_platforms?: number;
      pending_pairings?: number;
      approved_pairings?: number;
    };
  };
}

interface UpdateCheckResponse {
  ok: boolean;
  current_version: string;
  latest_version: string;
  has_update: boolean;
  project_url: string;
}

interface GatewayRestartResponse {
  ok: boolean;
  accepted?: boolean;
  message?: string;
}

interface SystemReloadResponse {
  ok: boolean;
  updated?: number;
  message?: string;
}

interface SystemDebugResponse {
  ok: boolean;
  mode?: 'upload' | 'local';
  report_url?: string | null;
  agent_log_url?: string | null;
  gateway_log_url?: string | null;
  failures?: string[];
  report?: string;
  agent_log?: string | null;
  gateway_log?: string | null;
}

interface SnapshotEntry {
  id: string;
  label?: string | null;
  file_count?: number;
  total_size?: number;
}

interface SnapshotListResponse {
  ok: boolean;
  snapshots: SnapshotEntry[];
}

interface SnapshotCreateResponse {
  ok: boolean;
  snapshot_id?: string;
  snapshot?: SnapshotEntry | null;
  message?: string;
}

interface SnapshotActionResponse {
  ok: boolean;
  snapshot_id?: string;
  deleted?: number;
  keep?: number;
  message?: string;
}

interface WorkspaceCheckpointResponse {
  ok: boolean;
  checkpoints?: Array<{ checkpoint_id?: string; label?: string }>;
}

interface WorkspaceRollbackResponse {
  ok: boolean;
  checkpoint_id?: string;
  message?: string;
}

interface SessionUsageResponse {
  ok: boolean;
  session_usage?: {
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number;
  };
}

const DEFAULT_ITEMS: TranscriptItem[] = [];
const CHAT_STATUSBAR_STORAGE_KEY = 'hermes-chat-statusbar-visible';

function readInitialStatusBarVisible(): boolean {
  try {
    const stored = window.localStorage.getItem(CHAT_STATUSBAR_STORAGE_KEY);
    if (stored == null) return true;
    return stored !== 'false';
  } catch {
    return true;
  }
}

function mapGuiEventToTranscriptItem(event: GuiEvent): TranscriptItem | null {
  const { type, payload } = event;

  if (type === 'tool.started') {
    const name = String(payload.tool_name ?? 'tool');
    const preview = String(payload.preview ?? '');
    return { role: 'tool', title: name, content: preview ? `started · ${preview}` : 'started' };
  }

  if (type === 'tool.completed') {
    const name = String(payload.tool_name ?? 'tool');
    const duration = payload.duration != null ? String(payload.duration) : '';
    const preview = String(payload.result_preview ?? '');
    const parts = [duration ? `completed in ${duration}` : 'completed', preview].filter(Boolean);
    return { role: 'tool', title: name, content: parts.join(' · ') };
  }

  if (type === 'run.failed') {
    const error = String(payload.error ?? payload.error_type ?? 'Run failed');
    return { role: 'system', title: 'Run failed', content: error };
  }

  if (type === 'run.started' || type === 'run.completed' || type === 'message.user' || type === 'message.assistant.completed' || type === 'message.assistant.delta') {
    // Handled specifically in subscribeToEvents or ignored
    return null;
  }

  return null;
}

export function ChatPage({ voiceMode }: { voiceMode?: boolean }) {
  const [items, setItems] = useState<TranscriptItem[]>(DEFAULT_ITEMS);
  const [runStatus, setRunStatus] = useState('connecting');
  const [sessionId, setSessionId] = useState('current');
  const [humanPending, setHumanPending] = useState<HumanRequest[]>([]);
  const [toolEvents, setToolEvents] = useState<string[]>([]);
  const [backendConnected, setBackendConnected] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [usageData, setUsageData] = useState<Record<string, any>>({});
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [reasoningEffort, setReasoningEffort] = useState<string>('none');
  const [showReasoning, setShowReasoning] = useState(false);
  const [toolProgressMode, setToolProgressMode] = useState<'off' | 'new' | 'all' | 'verbose'>('all');
  const [showStatusBar, setShowStatusBar] = useState<boolean>(() => readInitialStatusBarVisible());
  const [queuedPrompt, setQueuedPrompt] = useState<string | null>(null);
  const subscriptionRef = useRef<{ close(): void } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const voiceModeRef = useRef(voiceMode);
  const seenToolsRef = useRef<Set<string>>(new Set());
  const queuedPromptRef = useRef<string | null>(null);

  const resetChat = () => {
    if (subscriptionRef.current) subscriptionRef.current.close();
    window.location.hash = '#/chat';
    setSessionId('current');
    setItems([{ role: 'system', title: 'System', content: '✓ Ready. Send a message to start a new chat.' }]);
    setRunStatus('ready');
    setCurrentRunId(null);
    setToolEvents([]);
    setUsageData({});
    setQueuedPromptState(null);
  };

  const addSystemMessage = (content: string, title = 'System') => {
    setItems((prev) => [...prev, { role: 'system', title, content }]);
  };

  const setQueuedPromptState = (value: string | null) => {
    queuedPromptRef.current = value;
    setQueuedPrompt(value);
  };

  useEffect(() => {
    try {
      window.localStorage.setItem(CHAT_STATUSBAR_STORAGE_KEY, String(showStatusBar));
    } catch {
      // ignore persistence failures
    }
  }, [showStatusBar]);

  const dispatchUiRoute = (route: 'chat' | 'sessions' | 'workspace' | 'usage' | 'jobs' | 'skills' | 'memory' | 'missions' | 'commands') => {
    window.dispatchEvent(new CustomEvent('hermes:navigate', { detail: { route } }));
    window.location.hash = `#/${route}`;
  };

  const dispatchUiModal = (tab: 'settings' | 'tools' | 'gateway' | 'skills' | 'automations' | 'insights') => {
    window.dispatchEvent(new CustomEvent('hermes:openModal', { detail: { tab } }));
  };

  const dispatchUiDrawer = (tab: 'terminal' | 'processes' | 'logs' | 'browser') => {
    window.dispatchEvent(new CustomEvent('hermes:openDrawer', { detail: { tab } }));
  };

  const shouldDisplayToolEvent = (toolName: string) => {
    if (toolProgressMode === 'off') return false;
    if (toolProgressMode === 'new') {
      if (seenToolsRef.current.has(toolName)) return false;
      seenToolsRef.current.add(toolName);
    }
    return true;
  };

  // Sync voiceMode prop to ref for event callbacks
  useEffect(() => {
    voiceModeRef.current = voiceMode;
    if (!voiceMode && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
  }, [voiceMode]);

  useEffect(() => {
    apiClient.get<SettingsResponse>('/settings')
      .then((res) => {
        if (!res.ok || !res.settings) return;
        setReasoningEffort(String(res.settings.agent?.reasoning_effort ?? 'none'));
        setShowReasoning(Boolean(res.settings.display?.show_reasoning ?? false));
        const mode = String(res.settings.display?.tool_progress ?? 'all');
        if (mode === 'off' || mode === 'new' || mode === 'all' || mode === 'verbose') {
          setToolProgressMode(mode);
        }
      })
      .catch(() => {});
  }, []);

  // Sync state with Inspector Panels
  useEffect(() => {
    const handleRequest = () => {
      window.dispatchEvent(new CustomEvent('hermes-run-sync', {
        detail: { runId: currentRunId, status: runStatus }
      }));
      window.dispatchEvent(new CustomEvent('hermes-session-sync', {
        detail: { sessionId }
      }));
    };
    window.addEventListener('hermes-run-request-sync', handleRequest);
    handleRequest(); // Initial broadcast
    return () => window.removeEventListener('hermes-run-request-sync', handleRequest);
  }, [currentRunId, runStatus, sessionId]);

  const subscribeToEvents = (sid: string) => {
    if (subscriptionRef.current) {
      subscriptionRef.current.close();
    }

    seenToolsRef.current = new Set();
    setToolEvents([]);

    subscriptionRef.current = openSessionEventStream(sid, (event) => {
      let mapped: TranscriptItem | null = null;
      if (event.type === 'tool.started' || event.type === 'tool.completed') {
        const toolName = String(event.payload.tool_name ?? 'tool');
        if (shouldDisplayToolEvent(toolName)) {
          mapped = mapGuiEventToTranscriptItem(event);
        }
      } else {
        mapped = mapGuiEventToTranscriptItem(event);
      }
      if (mapped) {
        setItems((prev) => [...prev, mapped]);
      }

      if (event.type === 'message.reasoning.delta') {
        if (!showReasoning) return;
        const text = String(event.payload.content || '');
        if (text) {
          setItems((prev) => {
            const next = [...prev];
            // Find or create the current assistant message to attach reasoning
            let lastAssistantIdx = -1;
            for (let i = next.length - 1; i >= 0; i--) {
              if (next[i].role === 'assistant') {
                lastAssistantIdx = i;
                break;
              }
              if (next[i].role === 'user') break;
            }
            if (lastAssistantIdx >= 0) {
              next[lastAssistantIdx] = {
                ...next[lastAssistantIdx],
                reasoning: (next[lastAssistantIdx].reasoning || '') + text,
                isReasoningStreaming: true,
              };
            } else {
              // Reasoning arrived before content — create placeholder
              next.push({ role: 'assistant', title: 'Hermes', content: '', reasoning: text, isReasoningStreaming: true });
            }
            return next;
          });
        }
      } else if (event.type === 'message.assistant.delta') {
        const text = String(event.payload.content || '');
        if (text) {
          setItems((prev) => {
            const next = [...prev];
            const thinkingIdx = next.findIndex(i => i.content === '⏳ Hermes is thinking…');
            if (thinkingIdx >= 0) next.splice(thinkingIdx, 1);
            
            let lastAssistantIdx = -1;
            for (let i = next.length - 1; i >= 0; i--) {
              if (next[i].role === 'assistant') {
                lastAssistantIdx = i;
                break;
              }
              if (next[i].role === 'user') {
                break;
              }
            }
            if (lastAssistantIdx >= 0) {
              next[lastAssistantIdx] = { ...next[lastAssistantIdx], content: next[lastAssistantIdx].content + text, isStreaming: true, isReasoningStreaming: false };
            } else {
              next.push({ role: 'assistant', title: 'Hermes', content: text, isStreaming: true });
            }
            return next;
          });
        }
      } else if (event.type === 'message.assistant.completed') {
        const text = String(event.payload.content || '');
        const reasoning = showReasoning && event.payload.reasoning ? String(event.payload.reasoning) : undefined;
        setItems((prev) => {
          const next = [...prev];
          const thinkingIdx = next.findIndex(i => i.content === '⏳ Hermes is thinking…');
          if (thinkingIdx >= 0) next.splice(thinkingIdx, 1);
          
          let lastAssistantIdx = -1;
          for (let i = next.length - 1; i >= 0; i--) {
            if (next[i].role === 'assistant') {
              lastAssistantIdx = i;
              break;
            }
            if (next[i].role === 'user') {
              break;
            }
          }
          if (lastAssistantIdx >= 0) {
            next[lastAssistantIdx] = {
              ...next[lastAssistantIdx],
              content: text,
              isStreaming: false,
              isReasoningStreaming: false,
              // Prefer streamed reasoning, fall back to completed-payload reasoning
              reasoning: showReasoning ? (next[lastAssistantIdx].reasoning || reasoning) : undefined,
            };
          } else {
            next.push({ role: 'assistant', title: 'Hermes', content: text || '(no response text)', isStreaming: false, reasoning });
          }
          return next;
        });

        if (voiceModeRef.current && text && window.speechSynthesis) {
          // Cancel any ongoing speech
          window.speechSynthesis.cancel();
          const utterance = new SpeechSynthesisUtterance(text.slice(0, 1000)); // Speak up to 1000 chars to avoid memory issues
          window.speechSynthesis.speak(utterance);
        }
      }

      if (event.type === 'tool.started') {
        const name = String(event.payload.tool_name ?? 'tool');
        const preview = String(event.payload.preview ?? '');
        if (toolProgressMode !== 'off') {
          setToolEvents((prev) => {
            const entry = toolProgressMode === 'verbose' && preview
              ? `tool.started · ${name} · ${preview}`
              : `tool.started · ${name}`;
            if (toolProgressMode === 'all' || toolProgressMode === 'verbose') return [...prev, entry];
            return prev.includes(entry) ? prev : [...prev, entry];
          });
        }
      } else if (event.type === 'tool.completed') {
        const name = String(event.payload.tool_name ?? 'tool');
        const preview = String(event.payload.result_preview ?? '');
        if (toolProgressMode !== 'off') {
          setToolEvents((prev) => {
            const entry = toolProgressMode === 'verbose' && preview
              ? `tool.completed · ${name} · ${preview}`
              : `tool.completed · ${name}`;
            if (toolProgressMode === 'all' || toolProgressMode === 'verbose') return [...prev, entry];
            return prev.includes(entry) ? prev : [...prev, entry];
          });
        }
      } else if (event.type === 'run.completed') {
        setRunStatus('completed');
        if (event.payload.usage) {
          setUsageData((prev) => {
            const next = { ...prev };
            const usage = event.payload.usage as any;
            next.prompt_tokens = (next.prompt_tokens || 0) + (usage?.prompt_tokens || 0);
            next.completion_tokens = (next.completion_tokens || 0) + (usage?.completion_tokens || 0);
            next.total_tokens = (next.total_tokens || 0) + (usage?.total_tokens || 0);
            return next;
          });
          // Dispatch usage sync event for TopBar context meter
          window.dispatchEvent(new CustomEvent('hermes-usage-sync', {
            detail: { usage: event.payload.usage }
          }));
        }
        // Remove the "thinking" indicator
        setItems((prev) => {
          let idx = -1;
          for (let i = prev.length - 1; i >= 0; i--) {
            if (prev[i].content === '⏳ Hermes is thinking…') { idx = i; break; }
          }
          if (idx >= 0) {
            const next = [...prev];
            next.splice(idx, 1);
            return next;
          }
          return prev;
        });
        const pendingQueuedPrompt = queuedPromptRef.current;
        if (pendingQueuedPrompt) {
          setQueuedPromptState(null);
          setTimeout(() => {
            handleSend(pendingQueuedPrompt, [], false, false).catch(() => {});
          }, 0);
        }
      } else if (event.type === 'run.failed') {
        setRunStatus('failed');
        // Remove the "thinking" indicator on failure too
        setItems((prev) => {
          let idx = -1;
          for (let i = prev.length - 1; i >= 0; i--) {
            if (prev[i].content === '⏳ Hermes is thinking…') { idx = i; break; }
          }
          if (idx >= 0) {
            const next = [...prev];
            next.splice(idx, 1);
            return next;
          }
          return prev;
        });
      }
    });
  };

  const loadSession = async (sid: string) => {
    if (subscriptionRef.current) {
      subscriptionRef.current.close();
    }
    // Update hash for deep linking
    window.location.hash = `#/chat/${sid}`;
    
    setSessionId(sid);
    setRunStatus('completed');
    setCurrentRunId(null);
    setToolEvents([]);
    setUsageData({});
    setQueuedPromptState(null);
    try {
      const res = await apiClient.get<any>(`/sessions/${sid}/transcript`);
      if (res.ok && Array.isArray(res.items)) {
        const mappedItems: TranscriptItem[] = [];
        let curUsage: Record<string, any> | undefined;
        for (const item of res.items) {
          if (item.type === 'human') {
            mappedItems.push({ role: 'user', title: 'You', content: item.content || '' });
          } else if (item.type === 'assistant') {
            mappedItems.push({ role: 'assistant', title: 'Hermes', content: item.content || '' });
          } else if (item.type === 'run') {
            if (item.usage) curUsage = item.usage;
          }
        }
        setItems(mappedItems.length > 0 ? mappedItems : [{ role: 'system', title: 'System', content: 'Session loaded.' }]);
        if (curUsage) setUsageData(curUsage);
      }
    } catch (err) {
      console.error('Failed to load transcript:', err);
    }
    subscribeToEvents(sid);
  };

  const refreshPending = (active = true) => {
    apiClient
      .get<HumanPendingResponse>('/human/pending')
      .then((response) => {
        if (active && response.ok) {
          const normalized = response.pending.map((item: any) => ({
            ...item,
            id: String(item.id ?? item.request_id ?? ''),
            command: item.command ?? item.metadata?.command ?? item.prompt,
            message: item.message ?? item.prompt,
          }));
          setHumanPending(normalized);
        }
      })
      .catch(() => {});
  };

  useEffect(() => {
    let active = true;

    // Check URL hash for deep linking FIRST (e.g. #/chat/session-xyz)
    const hashParts = window.location.hash.replace('#/', '').split('/');
    const hasDeepLink = hashParts[0] === 'chat' && !!hashParts[1];

    // Check backend connectivity
    apiClient
      .get<{ status: string }>('/health')
      .then(() => {
        if (active) {
          setBackendConnected(true);
          // Only show generic connected message if NOT loading a deep-linked session
          if (!hasDeepLink) {
            setRunStatus('ready');
            setItems([{ role: 'system', title: 'System', content: '✓ Connected to Hermes backend. Send a message to start chatting.' }]);
          }
        }
      })
      .catch(() => {
        if (active) {
          setBackendConnected(false);
          setRunStatus('disconnected');
          setItems([{ role: 'system', title: 'System', content: '✗ Cannot reach Hermes backend. Make sure the gateway is running (API_SERVER_ENABLED=true python -m gateway.run).' }]);
        }
      });

    refreshPending(active);

    // Poll for pending human requests every 3 seconds
    const pollId = setInterval(() => {
      if (active) refreshPending(active);
    }, 3000);

    if (hasDeepLink) {
      loadSession(hashParts[1]);
    }

    return () => {
      active = false;
      clearInterval(pollId);
      if (subscriptionRef.current) {
        subscriptionRef.current.close();
      }
    };
  }, []);

  // Auto-scroll on new messages and during streaming content updates
  const lastItemContent = items.length > 0 ? items[items.length - 1].content : '';
  useEffect(() => {
    // scrollRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [items.length, lastItemContent]);

  useEffect(() => {
    if (sessionId && sessionId !== 'current') {
      subscribeToEvents(sessionId);
    }
  }, [sessionId, showReasoning, toolProgressMode]);

  const approvals = humanPending.filter((item) => item.kind === 'approval');
  const clarifications = humanPending.filter((item) => item.kind === 'clarify');

  const handleApprove = async (id: string, decision: 'once' | 'session' | 'always') => {
    await apiClient.post('/human/approve', { request_id: id, decision });
    refreshPending(true);
  };

  const handleDeny = async (id: string) => {
    await apiClient.post('/human/deny', { request_id: id });
    refreshPending(true);
  };

  const handleClarify = async (id: string, response: string) => {
    await apiClient.post('/human/clarify', { request_id: id, response });
    refreshPending(true);
  };

  const handleBranch = async (messageIndex: number) => {
    if (sessionId === 'current') return;
    try {
      const res = await apiClient.post<{ ok: boolean; session_id: string; title: string; message_count: number }>(`/sessions/${sessionId}/branch`, { at_message_index: messageIndex });
      if (res.ok && res.session_id) {
        setItems((prev) => [...prev, { role: 'system', title: 'System', content: `🌿 Branched session created: ${res.title} (${res.message_count} messages). Loading…` }]);
        setTimeout(() => loadSession(res.session_id), 500);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setItems((prev) => [...prev, { role: 'system', title: 'Error', content: `Failed to branch: ${msg}` }]);
    }
  };

  const handleSlashCommand = async (rawPrompt: string): Promise<boolean> => {
    const trimmed = rawPrompt.trim();
    if (!trimmed.startsWith('/')) return false;

    const [rawName, ...rest] = trimmed.slice(1).split(/\s+/);
    const command = rawName.toLowerCase();
    const args = rest.join(' ').trim();

    if (command === 'btw' || command === 'bg' || command === 'background') {
      return false;
    }

    if (command === 'new' || command === 'reset' || command === 'clear') {
      resetChat();
      return true;
    }

    if (command === 'help' || command === 'commands') {
      dispatchUiRoute('commands');
      try {
        const res = await apiClient.get<CommandsResponse>('/commands');
        if (res.ok) {
          const preview = res.commands
            .slice(0, 12)
            .map((entry) => `/${entry.name}${entry.aliases && entry.aliases.length ? ` (${entry.aliases.map((alias) => `/${alias}`).join(', ')})` : ''} — ${entry.description}`)
            .join('\n');
          addSystemMessage(`Opened Command Browser.\n\nAvailable commands: ${res.commands.length}\n\n${preview}`, 'Commands');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Failed to load command help: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'quit' || command === 'exit' || (command === 'q' && !args)) {
      addSystemMessage(
        'Hermes Web Console cannot terminate the browser tab directly. Close the browser tab/window if you want to leave the console, or use /new to reset the current chat.',
        'Quit',
      );
      return true;
    }

    if (command === 'statusbar' || command === 'sb') {
      let nextVisible = showStatusBar;
      if (!args || args === 'toggle') {
        nextVisible = !showStatusBar;
      } else if (['on', 'show'].includes(args)) {
        nextVisible = true;
      } else if (['off', 'hide'].includes(args)) {
        nextVisible = false;
      } else if (args === 'status') {
        addSystemMessage(`Status bar: ${showStatusBar ? 'visible' : 'hidden'}`, 'Status Bar');
        return true;
      } else {
        addSystemMessage('Usage: /statusbar [on|off|toggle|status]', 'Status Bar');
        return true;
      }
      setShowStatusBar(nextVisible);
      addSystemMessage(`Status bar ${nextVisible ? 'shown' : 'hidden'}.`, 'Status Bar');
      return true;
    }

    if (command === 'queue' || (command === 'q' && Boolean(args))) {
      if (!args) {
        addSystemMessage('Usage: /queue <prompt>', 'Queue');
        return true;
      }
      if (runStatus === 'running') {
        setQueuedPromptState(args);
        addSystemMessage(`Queued for next turn: ${args}`, 'Queue');
      } else {
        addSystemMessage('No active run — sending queued prompt immediately.', 'Queue');
        setTimeout(() => {
          handleSend(args, [], false, false).catch(() => {});
        }, 0);
      }
      return true;
    }

    if (command === 'history') {
      if (sessionId === 'current') {
        addSystemMessage('No active session yet. Send a message first, or open Sessions to browse saved chats.', 'History');
        dispatchUiRoute('sessions');
        return true;
      }
      try {
        const transcript = await apiClient.get<TranscriptResponse>(`/sessions/${sessionId}/transcript`);
        const lines = (transcript.items || [])
          .slice(-12)
          .map((item) => {
            const role = String(item.role || item.type || 'message');
            const content = String(item.content || '').replace(/\s+/g, ' ').trim();
            return `${role}: ${content}`;
          })
          .filter(Boolean);

        if (lines.length === 0) {
          addSystemMessage(`Session ${sessionId} has no transcript entries yet.`, 'History');
        } else {
          addSystemMessage(`Recent history for ${sessionId}:\n\n${lines.join('\n')}`, 'History');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Failed to load history: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'config') {
      try {
        const current = await apiClient.get<SettingsResponse>('/settings');
        const settings = current.settings || {};
        const summary = {
          model: settings.model,
          provider: settings.provider,
          agent: {
            reasoning_effort: settings.agent?.reasoning_effort,
            service_tier: settings.agent?.service_tier,
            verbose: settings.agent?.verbose,
            max_turns: settings.agent?.max_turns,
          },
          display: {
            show_reasoning: settings.display?.show_reasoning,
            tool_progress: settings.display?.tool_progress,
            streaming: settings.display?.streaming,
            skin: settings.display?.skin,
          },
          approvals: settings.approvals,
          terminal: settings.terminal,
          browser: settings.browser,
          timezone: settings.timezone,
        };
        addSystemMessage(`Effective config snapshot:\n\n${JSON.stringify(summary, null, 2)}`, 'Config');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Failed to load config: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'save') {
      if (sessionId === 'current') {
        addSystemMessage('Nothing to save yet. Start a chat first so Hermes has a session transcript to export.', 'Save');
        return true;
      }
      const backend = getBackendUrl();
      const exportUrl = `${backend ? `${backend}/api/gui` : '/api/gui'}/sessions/${encodeURIComponent(sessionId)}/export?format=json`;
      window.open(exportUrl, '_blank', 'noopener,noreferrer');
      addSystemMessage(`Export started for session ${sessionId}.`, 'Save');
      return true;
    }

    if (command === 'title') {
      if (!args) {
        addSystemMessage('Usage: /title <new session title>', 'Title');
        return true;
      }
      if (sessionId === 'current') {
        addSystemMessage('No active saved session yet. Send a message first, then try /title again.', 'Title');
        return true;
      }
      try {
        await apiClient.post(`/sessions/${encodeURIComponent(sessionId)}/title`, { title: args });
        addSystemMessage(`🏷️ Session renamed to "${args}".`, 'Title');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Title update failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'rollback') {
      try {
        if (!args) {
          const res = await apiClient.get<WorkspaceCheckpointResponse>('/workspace/checkpoints');
          const lines = (res.checkpoints || []).map((checkpoint, index) => {
            const id = checkpoint.checkpoint_id || 'unknown';
            const label = checkpoint.label || id;
            return `${index + 1}. ${label} (${id})`;
          });
          addSystemMessage(
            lines.length
              ? `Workspace checkpoints:\n\n${lines.join('\n')}\n\nUse /rollback <checkpoint_id> to restore one.`
              : 'No workspace checkpoints are available right now.',
            'Rollback',
          );
          return true;
        }

        const res = await apiClient.post<WorkspaceRollbackResponse>('/workspace/rollback', { checkpoint_id: args });
        addSystemMessage(res.message || `↩ Rolled workspace back to ${args}.`, 'Rollback');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Rollback failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'snapshot' || command === 'snap') {
      const snapshotParts = args.split(/\s+/).filter(Boolean);
      const snapshotSubcommand = (snapshotParts[0] || 'list').toLowerCase();

      try {
        if (snapshotSubcommand === 'list' || snapshotSubcommand === 'ls') {
          const res = await apiClient.get<SnapshotListResponse>('/system/snapshots?limit=20');
          const lines = (res.snapshots || []).map((snapshot, index) => {
            const size = Number(snapshot.total_size || 0);
            const sizeText = size < 1024
              ? `${size} B`
              : size < 1024 * 1024
                ? `${Math.round(size / 1024)} KB`
                : `${(size / 1024 / 1024).toFixed(1)} MB`;
            return `${index + 1}. ${snapshot.id} · ${snapshot.file_count ?? 0} file(s) · ${sizeText}${snapshot.label ? ` · ${snapshot.label}` : ''}`;
          });
          addSystemMessage(
            lines.length
              ? `Recent state snapshots:\n\n${lines.join('\n')}`
              : 'No state snapshots yet. Use /snapshot create [label] to make one.',
            'Snapshot',
          );
          return true;
        }

        if (snapshotSubcommand === 'create') {
          const label = snapshotParts.slice(1).join(' ').trim();
          const res = await apiClient.post<SnapshotCreateResponse>('/system/snapshots', label ? { label } : {});
          addSystemMessage(
            `${res.message || `Snapshot created: ${res.snapshot_id}`}${res.snapshot?.file_count != null ? `\n\nFiles captured: ${res.snapshot.file_count}` : ''}`,
            'Snapshot',
          );
          return true;
        }

        if (snapshotSubcommand === 'restore' || snapshotSubcommand === 'rewind') {
          const snapshotId = snapshotParts[1] || '';
          if (!snapshotId) {
            addSystemMessage('Usage: /snapshot restore <snapshot-id>', 'Snapshot');
            return true;
          }
          const res = await apiClient.post<SnapshotActionResponse>('/system/snapshots/restore', { snapshot_id: snapshotId });
          addSystemMessage(res.message || `Restored snapshot ${snapshotId}.`, 'Snapshot');
          return true;
        }

        if (snapshotSubcommand === 'prune') {
          const rawKeep = snapshotParts[1];
          const keep = rawKeep ? Number(rawKeep) : 20;
          if (!Number.isFinite(keep) || keep < 1) {
            addSystemMessage('Usage: /snapshot prune [keep-count]', 'Snapshot');
            return true;
          }
          const res = await apiClient.post<SnapshotActionResponse>('/system/snapshots/prune', { keep });
          addSystemMessage(res.message || `Pruned ${res.deleted ?? 0} snapshot(s).`, 'Snapshot');
          return true;
        }

        addSystemMessage('Usage: /snapshot [list|create [label]|restore <id>|prune [N]]', 'Snapshot');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Snapshot command failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'reload') {
      try {
        const res = await apiClient.post<SystemReloadResponse>('/system/reload', {});
        addSystemMessage(res.message || `Reloaded .env (${res.updated ?? 0} var(s) updated)`, 'Reload');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Reload failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'reload-mcp' || command === 'reload_mcp') {
      try {
        const res = await apiClient.post<{ success?: boolean; reloaded?: boolean; tools_count?: number; servers_count?: number }>('/mcp/reload', {});
        if (res.success === false) {
          addSystemMessage('MCP reload was rejected by the backend.', 'MCP');
        } else {
          addSystemMessage(
            `🔌 Reloaded MCP servers${res.servers_count != null ? ` (${res.servers_count} server(s)` : ''}${res.tools_count != null ? `${res.servers_count != null ? ', ' : ' ('}${res.tools_count} tool(s)` : ''}${res.servers_count != null || res.tools_count != null ? ')' : ''}.`,
            'MCP',
          );
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`MCP reload failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'debug') {
      const local = args === 'local';
      try {
        const res = await apiClient.post<SystemDebugResponse>('/system/debug', { local });
        if (res.mode === 'local') {
          addSystemMessage(`Local debug report:\n\n${res.report || '(empty report)'}`, 'Debug');
        } else {
          const lines = [
            res.report_url ? `Report: ${res.report_url}` : null,
            res.agent_log_url ? `agent.log: ${res.agent_log_url}` : null,
            res.gateway_log_url ? `gateway.log: ${res.gateway_log_url}` : null,
            res.failures && res.failures.length ? `Failed uploads: ${res.failures.join(', ')}` : null,
          ].filter(Boolean);
          addSystemMessage(`Debug report ready.${lines.length ? `\n\n${lines.join('\n')}` : ''}`, 'Debug');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Debug report failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'platforms' || command === 'gateway') {
      try {
        const [platformsRes, overviewRes] = await Promise.all([
          apiClient.get<GatewayPlatformsResponse>('/gateway/platforms'),
          apiClient.get<GatewayOverviewResponse>('/gateway/overview').catch(() => ({ ok: false } as GatewayOverviewResponse)),
        ]);
        const summary = overviewRes.overview?.summary;
        const lines = platformsRes.platforms.map((platform) => {
          const state = platform.runtime_state || (platform.enabled ? 'enabled' : 'disabled');
          const detail = platform.error_message || (platform.configured ? 'configured' : 'not configured');
          return `${platform.label} (${platform.key}) — ${state}; ${detail}`;
        });
        const header = summary
          ? `Platforms: total=${summary.platform_count ?? 0}, enabled=${summary.enabled_platforms ?? 0}, connected=${summary.connected_platforms ?? 0}, pending_pairings=${summary.pending_pairings ?? 0}`
          : 'Gateway platforms:';
        addSystemMessage(`${header}\n\n${lines.join('\n')}`, 'Platforms');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Failed to load platform status: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'sethome' || command === 'set-home') {
      if (!args) {
        dispatchUiModal('gateway');
        addSystemMessage(
          'Usage: /sethome <platform> <chat_id>\n\nExample: /sethome discord 1234567890\nThis sets the platform home channel using the existing gateway config surface.',
          'Set Home',
        );
        return true;
      }

      const [platform, ...chatIdParts] = args.split(/\s+/).filter(Boolean);
      const chatId = chatIdParts.join(' ').trim();
      if (!platform || !chatId) {
        addSystemMessage('Usage: /sethome <platform> <chat_id>', 'Set Home');
        return true;
      }

      try {
        const res = await apiClient.patch<any>(`/gateway/platforms/${encodeURIComponent(platform)}/config`, { home_channel: chatId });
        if (res?.ok) {
          addSystemMessage(
            `✅ Home channel for ${platform} set to ${chatId}.${res.reload_required ? ' Restart Hermes for changes to fully apply.' : ''}`,
            'Set Home',
          );
        } else {
          addSystemMessage(`Failed to set home channel for ${platform}.`, 'Error');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Set home failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'restart') {
      try {
        const res = await apiClient.post<GatewayRestartResponse>('/gateway/restart', {});
        if (res.ok && res.accepted) {
          addSystemMessage(res.message || 'Gateway restart requested. Active runs will drain before restart.', 'Restart');
        } else {
          addSystemMessage(res.message || 'Gateway restart was not accepted because one is already in progress.', 'Restart');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Restart failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'update') {
      try {
        const res = await apiClient.get<UpdateCheckResponse>('/version/check');
        if (!res.ok) {
          addSystemMessage('Update check failed.', 'Update');
          return true;
        }
        if (res.has_update) {
          window.open(res.project_url, '_blank', 'noopener,noreferrer');
          addSystemMessage(`Update available: ${res.current_version} → ${res.latest_version}. Opened project page for upgrade instructions.`, 'Update');
        } else {
          addSystemMessage(`Hermes is up to date (${res.current_version}).`, 'Update');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Update check failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'image') {
      window.dispatchEvent(new CustomEvent('hermes:composerOpenFilePicker'));
      addSystemMessage('Opened the file picker. Choose an image to attach to your next prompt.', 'Image');
      return true;
    }

    if (command === 'paste') {
      const pasteResult = await new Promise<boolean>((resolve) => {
        const timeout = window.setTimeout(() => {
          window.removeEventListener('hermes:composerPasteResult', handlePasteResult as EventListener);
          resolve(false);
        }, 3000);

        const handlePasteResult = (event: Event) => {
          window.clearTimeout(timeout);
          window.removeEventListener('hermes:composerPasteResult', handlePasteResult as EventListener);
          const customEvent = event as CustomEvent<{ success?: boolean }>;
          resolve(Boolean(customEvent.detail?.success));
        };

        window.addEventListener('hermes:composerPasteResult', handlePasteResult as EventListener, { once: true });
        window.dispatchEvent(new CustomEvent('hermes:composerPasteClipboard'));
      });

      addSystemMessage(
        pasteResult
          ? 'Pasted image from clipboard into composer attachments.'
          : 'No image found in clipboard, or clipboard access was denied.',
        'Paste',
      );
      return true;
    }

    if (command === 'resume') {
      if (!args) {
        dispatchUiRoute('sessions');
        addSystemMessage('Opened Sessions. Use the session list to resume a previous chat, or run /resume <session id or title>.', 'Resume');
        return true;
      }
      try {
        const [sessionsRes, searchRes] = await Promise.all([
          apiClient.get<SessionsListResponse>('/sessions').catch(() => ({ ok: false, sessions: [] } as SessionsListResponse)),
          apiClient.get<SessionSearchResponse>(`/session-search?q=${encodeURIComponent(args)}`).catch(() => ({ ok: false } as SessionSearchResponse)),
        ]);

        const normalizedArgs = args.trim().toLowerCase();
        const exactSession = (sessionsRes.sessions || []).find((session) =>
          session.session_id.toLowerCase() === normalizedArgs || String(session.title || '').trim().toLowerCase() === normalizedArgs,
        );
        const fuzzySessionId = exactSession?.session_id || searchRes.search?.results?.[0]?.session_id;

        if (!fuzzySessionId) {
          addSystemMessage(`No session matched "${args}". Opened Sessions so you can choose manually.`, 'Resume');
          dispatchUiRoute('sessions');
          return true;
        }

        await apiClient.post(`/sessions/${fuzzySessionId}/resume`, {});
        await loadSession(fuzzySessionId);
        addSystemMessage(`↻ Resumed session ${fuzzySessionId}.`, 'Resume');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Resume failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'branch' || command === 'fork') {
      if (sessionId === 'current') {
        addSystemMessage('No active saved session to branch yet. Send a message first, then try /branch again.', 'Branch');
        return true;
      }
      try {
        const res = await apiClient.post<{ ok: boolean; session_id: string; title: string; message_count: number }>(`/sessions/${sessionId}/branch`, {});
        if (res.ok && args) {
          await apiClient.post(`/sessions/${res.session_id}/title`, { title: args });
        }
        const branchTitle = args || res.title;
        addSystemMessage(`🌿 Branched session created: ${branchTitle} (${res.message_count} messages). Loading…`, 'Branch');
        setTimeout(() => loadSession(res.session_id), 300);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Branch failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'approve') {
      const pendingApproval = humanPending.find((item) => item.kind === 'approval');
      if (!pendingApproval) {
        addSystemMessage('There is no pending approval request right now.', 'Approve');
        return true;
      }
      const decision = args === 'session' || args === 'always' ? args : 'once';
      try {
        await handleApprove(pendingApproval.id, decision);
        addSystemMessage(`Approved pending request (${decision}).`, 'Approve');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Approve failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'deny') {
      const pendingRequest = humanPending[0];
      if (!pendingRequest) {
        addSystemMessage('There is no pending approval or clarification request right now.', 'Deny');
        return true;
      }
      try {
        await handleDeny(pendingRequest.id);
        addSystemMessage(`Denied pending ${pendingRequest.kind} request.`, 'Deny');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Deny failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'usage') {
      try {
        if (sessionId !== 'current') {
          const res = await apiClient.get<SessionUsageResponse>(`/usage/session/${encodeURIComponent(sessionId)}`);
          const usage = res.session_usage;
          if (res.ok && usage) {
            addSystemMessage(
              [
                `Session usage for ${sessionId}:`,
                `Input tokens: ${usage.input_tokens.toLocaleString()}`,
                `Output tokens: ${usage.output_tokens.toLocaleString()}`,
                `Cache read: ${usage.cache_read_tokens.toLocaleString()}`,
                `Cache write: ${usage.cache_write_tokens.toLocaleString()}`,
                `Total tokens: ${usage.total_tokens.toLocaleString()}`,
                `Estimated cost: $${(usage.estimated_cost_usd || 0).toFixed(4)}`,
              ].join('\n'),
              'Usage',
            );
            return true;
          }
        }

        const promptTokens = Number(usageData.prompt_tokens || 0);
        const completionTokens = Number(usageData.completion_tokens || 0);
        const totalTokens = Number(usageData.total_tokens || 0);
        if (promptTokens || completionTokens || totalTokens) {
          addSystemMessage(
            [
              `Current session usage:`,
              `Prompt tokens: ${promptTokens.toLocaleString()}`,
              `Completion tokens: ${completionTokens.toLocaleString()}`,
              `Total tokens: ${totalTokens.toLocaleString()}`,
            ].join('\n'),
            'Usage',
          );
        } else {
          dispatchUiRoute('usage');
          addSystemMessage('Opened Usage. No inline token data is available for this session yet.', 'Usage');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Failed to load usage: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'skills') {
      dispatchUiRoute('skills');
      addSystemMessage('Opened Skills.', 'Navigation');
      return true;
    }

    if (command === 'tools' || command === 'toolsets') {
      dispatchUiModal('tools');
      addSystemMessage('Opened Tools & Toolsets in Control Center.', 'Navigation');
      return true;
    }

    if (command === 'cron') {
      dispatchUiModal('automations');
      addSystemMessage('Opened Automations (Cron).', 'Navigation');
      return true;
    }

    if (command === 'insights') {
      dispatchUiModal('insights');
      addSystemMessage('Opened Analytics & Insights.', 'Navigation');
      return true;
    }

    if (command === 'browser') {
      dispatchUiDrawer('browser');
      addSystemMessage('Opened Browser controls in the bottom drawer.', 'Navigation');
      return true;
    }

    if (command === 'voice') {
      window.dispatchEvent(new CustomEvent('hermes:toggleVoiceMode'));
      addSystemMessage(`Voice mode ${voiceModeRef.current ? 'toggle requested off' : 'toggle requested on'}.`, 'Voice');
      return true;
    }

    if (command === 'fast') {
      try {
        const current = await apiClient.get<SettingsResponse>('/settings');
        const currentMode = String(current.settings?.agent?.service_tier ?? 'normal').toLowerCase();

        if (!args || args === 'status') {
          addSystemMessage(`Fast mode: ${currentMode === 'fast' ? 'fast' : 'normal'}`, 'Fast Mode');
          return true;
        }

        if (['fast', 'on'].includes(args)) {
          await apiClient.patch('/settings', { agent: { service_tier: 'fast' } });
          addSystemMessage('⚡ Fast mode enabled. It will apply on the next message.', 'Fast Mode');
          return true;
        }

        if (['normal', 'off'].includes(args)) {
          await apiClient.patch('/settings', { agent: { service_tier: 'normal' } });
          addSystemMessage('⚡ Fast mode disabled. Normal mode will apply on the next message.', 'Fast Mode');
          return true;
        }

        addSystemMessage('Usage: /fast [normal|fast|status]', 'Fast Mode');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Fast mode update failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'yolo') {
      const activeSessionId = sessionId === 'current' ? '' : sessionId;
      if (!activeSessionId) {
        addSystemMessage('YOLO mode is session-scoped. Start or open a session first, then try /yolo again.');
        return true;
      }
      try {
        const current = await apiClient.get<YoloResponse>(`/human/yolo?session_id=${encodeURIComponent(activeSessionId)}`);
        const nextEnabled = args === 'on' ? true : args === 'off' ? false : !current.enabled;
        const updated = await apiClient.post<YoloResponse>('/human/yolo', { session_id: activeSessionId, enabled: nextEnabled });
        addSystemMessage(
          updated.enabled
            ? '⚡ YOLO mode ON for this session — dangerous commands will auto-approve.'
            : '⚠️ YOLO mode OFF for this session — dangerous commands will require approval.',
          'YOLO',
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`YOLO update failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (['model', 'provider', 'personality', 'skin', 'plugins', 'reload-mcp', 'config', 'profile'].includes(command)) {
      dispatchUiModal('settings');
      addSystemMessage(`Opened Settings for /${command}${args ? ` ${args}` : ''}.`, 'Navigation');
      return true;
    }

    if (command === 'reasoning') {
      try {
        if (!args || args === 'status') {
          addSystemMessage(`Reasoning effort: ${reasoningEffort}\nReasoning display: ${showReasoning ? 'on' : 'off'}`, 'Reasoning');
          return true;
        }

        if (['show', 'on'].includes(args)) {
          await apiClient.patch('/settings', { display: { show_reasoning: true } });
          setShowReasoning(true);
          addSystemMessage('🧠 Reasoning display enabled.', 'Reasoning');
          return true;
        }

        if (['hide', 'off'].includes(args)) {
          await apiClient.patch('/settings', { display: { show_reasoning: false } });
          setShowReasoning(false);
          addSystemMessage('🧠 Reasoning display disabled.', 'Reasoning');
          return true;
        }

        if (['none', 'minimal', 'low', 'medium', 'high', 'xhigh'].includes(args)) {
          await apiClient.patch('/settings', { agent: { reasoning_effort: args } });
          setReasoningEffort(args);
          addSystemMessage(`🧠 Reasoning effort set to ${args}. It will apply on the next message.`, 'Reasoning');
          return true;
        }

        addSystemMessage('Usage: /reasoning [none|minimal|low|medium|high|xhigh|show|hide|status]', 'Reasoning');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Reasoning update failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'verbose') {
      try {
        const cycle: Array<'off' | 'new' | 'all' | 'verbose'> = ['off', 'new', 'all', 'verbose'];
        const currentIdx = cycle.indexOf(toolProgressMode);
        const nextMode = currentIdx >= 0 ? cycle[(currentIdx + 1) % cycle.length] : 'all';
        const selectedMode = (args === 'off' || args === 'new' || args === 'all' || args === 'verbose')
          ? args
          : args === 'status'
            ? toolProgressMode
            : nextMode;

        if (args === 'status') {
          addSystemMessage(`Tool progress mode: ${toolProgressMode}`, 'Verbose');
          return true;
        }

        await apiClient.patch('/settings', { display: { tool_progress: selectedMode } });
        if (selectedMode === 'off' || selectedMode === 'new' || selectedMode === 'all' || selectedMode === 'verbose') {
          setToolProgressMode(selectedMode);
        }
        addSystemMessage(`⚙️ Tool progress set to ${selectedMode}.`, 'Verbose');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Verbose update failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'status') {
      addSystemMessage(
        [
          `Session: ${sessionId}`,
          `Run status: ${runStatus}`,
          `Run id: ${currentRunId ?? 'none'}`,
          `Queued prompt: ${queuedPrompt ?? 'none'}`,
          `Reasoning effort: ${reasoningEffort}`,
          `Reasoning display: ${showReasoning ? 'on' : 'off'}`,
          `Tool progress: ${toolProgressMode}`,
          `Voice mode: ${voiceModeRef.current ? 'on' : 'off'}`,
        ].join('\n'),
        'Status',
      );
      return true;
    }

    if (command === 'retry' && currentRunId) {
      try {
        const res = await apiClient.post<ChatSendResponse>('/chat/retry', { run_id: currentRunId });
        setCurrentRunId(res.run_id);
        setRunStatus('running');
        addSystemMessage('🔄 Retrying…');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Retry failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'undo') {
      try {
        const res = await apiClient.post<any>('/chat/undo', { session_id: sessionId, run_id: currentRunId });
        if (res.supported) {
          setItems((prev) => prev.slice(0, -1));
        } else {
          addSystemMessage('Undo is not yet supported for web runs. Use branching to fork from an earlier point.');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Undo failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'stop' && currentRunId) {
      try {
        const res = await apiClient.post<any>('/chat/stop', { run_id: currentRunId });
        if (res.supported && res.stop_requested) {
          setRunStatus('stopped');
          addSystemMessage('⏹ Stop requested.');
        } else {
          addSystemMessage('Stop is not supported for this run. It will complete naturally.');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Stop failed: ${msg}`, 'Error');
      }
      return true;
    }

    if (command === 'compress') {
      if (sessionId === 'current') {
        addSystemMessage('Compression needs an active saved session. Send a message first, then try again.');
        return true;
      }
      try {
        const payload: { session_id: string; focus_topic?: string } = { session_id: sessionId };
        if (args) payload.focus_topic = args;
        const res = await apiClient.post<{ok: boolean, compressed: boolean, reason?: string, original_length?: number, new_length?: number, focus_topic?: string}>('/chat/compress', payload);
        if (res.compressed) {
          await loadSession(sessionId);
          addSystemMessage(`✓ Context compressed from ${res.original_length} to ${res.new_length} messages${res.focus_topic ? ` (focus: "${res.focus_topic}")` : ''}.`);
        } else {
          addSystemMessage(`Compression skipped: ${res.reason}`);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addSystemMessage(`Compression failed: ${msg}`, 'Error');
      }
      return true;
    }

    addSystemMessage(`/${command} is not wired up in the web command dispatcher yet. Use /help for available commands or the dedicated GUI surfaces.`, 'Command');
    return true;
  };

  useEffect(() => {
    const handleExecuteCommand = (event: Event) => {
      const customEvent = event as CustomEvent<{ value?: string }>;
      const value = String(customEvent.detail?.value || '').trim();
      if (!value) return;
      handleSlashCommand(value).catch(() => {});
    };

    window.addEventListener('hermes:executeCommand', handleExecuteCommand);
    return () => window.removeEventListener('hermes:executeCommand', handleExecuteCommand);
  }, [sessionId, runStatus, currentRunId, humanPending, reasoningEffort, showReasoning, toolProgressMode, queuedPrompt, usageData]);

  const handleSend = async (prompt: string, attachments: AttachedFile[] = [], isBackground?: boolean, isQuickAsk?: boolean) => {
    if (!attachments.length) {
      const intercepted = await handleSlashCommand(prompt);
      if (intercepted) return;
    }

    setItems((prev) => [...prev, { role: 'user', title: isQuickAsk ? 'You (Quick Ask)' : 'You', content: prompt + (attachments.length ? ` [${attachments.length} file(s) attached]` : '') }]);
    setRunStatus('sending…');

    // Upload attachments first
    const uploadedPaths: string[] = [];
    const transcriptions: string[] = [];
    const audioExts = ['.mp3', '.wav', '.ogg', '.webm', '.m4a', '.aac', '.flac'];

    for (const att of attachments) {
      try {
        const result = await apiClient.upload<{ ok: boolean; media: { file_path: string } }>('/media/upload', att.file);
        if (result.ok) {
          uploadedPaths.push(result.media.file_path);

          // Auto-transcribe audio files
          const lowerName = att.file.name.toLowerCase();
          if (audioExts.some((ext) => lowerName.endsWith(ext))) {
            try {
              const txRes = await apiClient.post<{ ok: boolean; transcription?: { text?: string } }>('/media/transcribe', { file_path: result.media.file_path });
              if (txRes.ok && txRes.transcription?.text) {
                transcriptions.push(txRes.transcription.text);
              }
            } catch { /* transcription optional */ }
          }
        }
      } catch {
        // Upload failed, skip this attachment
      }
    }

    // Enrich prompt with file paths and transcriptions
    let enrichedPrompt = prompt;
    if (uploadedPaths.length > 0) {
      enrichedPrompt = `${prompt}\n\n[Attached files: ${uploadedPaths.join(', ')}]`;
    }
    if (transcriptions.length > 0) {
      enrichedPrompt += `\n\n[Audio transcription: ${transcriptions.join(' | ')}]`;
    }

    const sid = sessionId === 'current' ? `session-${Date.now()}` : sessionId;
    if (sessionId === 'current' && !isBackground && !isQuickAsk) {
      setSessionId(sid);
      subscribeToEvents(sid);
    } else if (sessionId === 'current' && isQuickAsk) {
      // Must subscribe to receive quick ask ephemeral stream
      setSessionId(sid);
      subscribeToEvents(sid);
    }

    try {
      let endpoint = '/chat/send';
      if (isQuickAsk) endpoint = '/chat/btw';
      else if (isBackground) endpoint = '/chat/background';

      const response = await apiClient.post<ChatSendResponse>(endpoint, {
        session_id: isBackground ? undefined : sid,
        prompt: enrichedPrompt
      });
      
      if (!isBackground) {
        setSessionId(response.session_id);
        setCurrentRunId(response.run_id);
        setRunStatus('running');
        setToolEvents((prev) => [...prev, `run started · ${response.run_id.slice(0, 8)}`]);
        setItems((prev) => [...prev, { role: 'system', title: 'System', content: isQuickAsk ? '⚡ Hermes is answering…' : '⏳ Hermes is thinking…' }]);
      } else {
        setItems((prev) => [...prev, { role: 'system', title: 'Background Run', content: `✓ Task silently dispatched to background session (${response.session_id.substring(0, 8)}).` }]);
        setRunStatus('ready');
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      setItems((prev) => [
        ...prev,
        {
          role: 'system',
          title: 'Error',
          content: `Failed to send message: ${msg}`
        }
      ]);
      setRunStatus('error');
    }
  };

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {sidebarOpen && (
        <SessionSidebar 
           activeSessionId={sessionId === 'current' ? null : sessionId}
           onSelectSession={loadSession}
           onNewChat={resetChat}
        />
      )}
      <div className="chat-layout" style={{ flex: 1, position: 'relative', display: 'grid', gridTemplateColumns: toolEvents.length > 0 ? 'minmax(0, 1fr) 280px' : 'minmax(0, 1fr)', gap: '16px' }}>
        {/* Toggle button on top-left of chat layout */}
        <button 
           onClick={() => setSidebarOpen(!sidebarOpen)} 
           style={{ position: 'absolute', top: '16px', left: '16px', background: 'rgba(255,255,255,0.1)', border: 'none', color: '#e2e8f0', cursor: 'pointer', fontSize: '1rem', zIndex: 10, padding: '4px 8px', borderRadius: '4px' }}
           title="Toggle Sidebar"
        >
           ☰
        </button>
        <div className="chat-main-column" style={{ paddingLeft: '48px', minWidth: 0 }}>
          {showStatusBar && (
            <div style={{ padding: '16px 20px 0' }}>
              <RunStatusBar status={runStatus} sessionId={sessionId} />
            </div>
          )}
          <Transcript items={items} sessionId={sessionId} onBranch={handleBranch} />
          <div ref={scrollRef} />

        {/* Action Prompts (only visible when pending) */}
        {approvals.length > 0 && <ApprovalPrompt pending={approvals} onApprove={handleApprove} onDeny={handleDeny} />}
        {clarifications.length > 0 && <ClarifyPrompt pending={clarifications} onClarify={handleClarify} onDeny={handleDeny} />}

        {/* Chat Flow Controls */}
        <div style={{ display: 'flex', gap: '8px', padding: '0 20px', justifyContent: 'center' }}>
          {runStatus === 'running' && (
            <button
              type="button"
              onClick={async () => {
                if (!currentRunId) return;
                try {
                  const res = await apiClient.post<any>('/chat/stop', { run_id: currentRunId });
                  if (res.supported && res.stop_requested) {
                    setRunStatus('stopped');
                    setItems((prev) => [...prev, { role: 'system', title: 'System', content: '⏹ Stop requested.' }]);
                  } else {
                    setItems((prev) => [...prev, { role: 'system', title: 'System', content: 'ℹ️ Stop is not supported for this run. It will complete naturally.' }]);
                  }
                } catch { /* ignore */ }
              }}
              style={{ padding: '6px 16px', borderRadius: '20px', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#fca5a5', cursor: 'pointer', fontSize: '0.85rem' }}
            >
              ⏹ Stop
            </button>
          )}
          {(runStatus === 'failed' || runStatus === 'completed') && currentRunId && (
            <button
              type="button"
              onClick={async () => {
                try {
                  const res = await apiClient.post<ChatSendResponse>('/chat/retry', { run_id: currentRunId });
                  setCurrentRunId(res.run_id);
                  setRunStatus('running');
                  setItems((prev) => [...prev, { role: 'system', title: 'System', content: '🔄 Retrying…' }]);
                } catch { /* ignore */ }
              }}
              style={{ padding: '6px 16px', borderRadius: '20px', background: 'rgba(56, 189, 248, 0.15)', border: '1px solid rgba(56, 189, 248, 0.3)', color: '#7dd3fc', cursor: 'pointer', fontSize: '0.85rem' }}
            >
              🔄 Retry
            </button>
          )}
          {items.length > 1 && (runStatus === 'completed' || runStatus === 'ready') && (
            <button
              type="button"
              onClick={async () => {
                try {
                  const res = await apiClient.post<any>('/chat/undo', { session_id: sessionId, run_id: currentRunId });
                  if (res.supported) {
                    setItems((prev) => prev.slice(0, -1));
                  } else {
                    setItems((prev) => [...prev, { role: 'system', title: 'System', content: 'ℹ️ Undo is not yet supported. Use session branching to fork from an earlier point.' }]);
                  }
                } catch { /* ignore */ }
              }}
              style={{ padding: '6px 16px', borderRadius: '20px', background: 'rgba(251, 191, 36, 0.15)', border: '1px solid rgba(251, 191, 36, 0.3)', color: '#fde68a', cursor: 'pointer', fontSize: '0.85rem' }}
            >
              ↩ Undo
            </button>
          )}
          {items.length > 5 && sessionId !== 'current' && (runStatus === 'completed' || runStatus === 'ready') && (
            <button
              type="button"
              onClick={async () => {
                const focusTopic = window.prompt(
                  '🗜️ Guided Compression\n\nOptionally enter a focus topic to preserve context around (leave empty for general compression):',
                  ''
                );
                // User cancelled
                if (focusTopic === null) return;
                
                setItems((prev) => [...prev, { role: 'system', title: 'System', content: focusTopic ? `🗜️ Compressing context (focus: "${focusTopic}")...` : '🗜️ Compressing context...' }]);
                try {
                  const payload: any = { session_id: sessionId };
                  if (focusTopic.trim()) payload.focus_topic = focusTopic.trim();
                  
                  const res = await apiClient.post<{ok: boolean, compressed: boolean, reason?: string, original_length?: number, new_length?: number, focus_topic?: string}>('/chat/compress', payload);
                  if (res.compressed) {
                    await loadSession(sessionId);
                    const focusNote = res.focus_topic ? ` (focus: "${res.focus_topic}")` : '';
                    setItems((prev) => [...prev, { role: 'system', title: 'System', content: `✓ Context compressed from ${res.original_length} to ${res.new_length} messages${focusNote}.` }]);
                  } else {
                    setItems((prev) => [...prev, { role: 'system', title: 'System', content: `ℹ️ Compression skipped: ${res.reason}` }]);
                  }
                } catch (err) {
                    const msg = err instanceof Error ? err.message : String(err);
                    setItems((prev) => [...prev, { role: 'system', title: 'Error', content: `Failed to compress: ${msg}` }]);
                }
              }}
              style={{ padding: '6px 16px', borderRadius: '20px', background: 'rgba(74, 222, 128, 0.15)', border: '1px solid rgba(74, 222, 128, 0.3)', color: '#4ade80', cursor: 'pointer', fontSize: '0.85rem' }}
            >
              🗜️ Compress
            </button>
          )}
        </div>

        <Composer onSend={handleSend} onNewChat={resetChat}
        reasoningEffort={reasoningEffort}
        onReasoningChange={(val) => {
          setReasoningEffort(val);
          apiClient.patch('/settings', { agent: { reasoning_effort: val } }).catch(() => {});
        }}
        />
        {showStatusBar && <UsageBar usage={usageData} />}
      </div>
      {toolEvents.length > 0 && (
        <div style={{ minWidth: 0, paddingRight: '12px', paddingTop: '56px' }}>
          <ToolTimeline events={toolEvents} />
        </div>
      )}
    </div>
    </div>
  );
}
