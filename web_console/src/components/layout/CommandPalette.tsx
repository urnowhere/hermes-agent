import { useEffect, useMemo, useState } from 'react';
import { apiClient } from '../../lib/api';
import type { DrawerTab, ModalTab, PrimaryRoute } from '../../lib/types';

type PaletteAction =
  | { id: string; group: 'pinned' | 'recent' | 'navigate' | 'panels' | 'commands'; kind: 'route'; label: string; hint: string; keywords: string[]; route: PrimaryRoute }
  | { id: string; group: 'pinned' | 'recent' | 'navigate' | 'panels' | 'commands'; kind: 'modal'; label: string; hint: string; keywords: string[]; tab: ModalTab }
  | { id: string; group: 'pinned' | 'recent' | 'navigate' | 'panels' | 'commands'; kind: 'drawer'; label: string; hint: string; keywords: string[]; tab: DrawerTab }
  | { id: string; group: 'pinned' | 'recent' | 'navigate' | 'panels' | 'commands'; kind: 'command'; label: string; hint: string; keywords: string[]; command: string; executeDirectly?: boolean };

interface CommandEntry {
  name: string;
  description: string;
  aliases: string[];
}

interface CommandsResponse {
  ok: boolean;
  commands: CommandEntry[];
}

interface CommandPaletteProps {
  open: boolean;
  onClose(): void;
}

const CORE_ACTIONS: PaletteAction[] = [
  { id: 'route-chat', group: 'navigate', kind: 'route', label: 'Open Chat', hint: 'Jump to the main chat workspace', keywords: ['chat', 'conversation', 'messages'], route: 'chat' },
  { id: 'route-sessions', group: 'navigate', kind: 'route', label: 'Open Sessions', hint: 'Browse and resume prior sessions', keywords: ['sessions', 'history', 'resume'], route: 'sessions' },
  { id: 'route-workspace', group: 'navigate', kind: 'route', label: 'Open Workspace', hint: 'Inspect files, diffs, and checkpoints', keywords: ['workspace', 'files', 'diff'], route: 'workspace' },
  { id: 'route-usage', group: 'navigate', kind: 'route', label: 'Open Usage', hint: 'See token and cost analytics', keywords: ['usage', 'tokens', 'cost'], route: 'usage' },
  { id: 'route-jobs', group: 'navigate', kind: 'route', label: 'Open Background Jobs', hint: 'Inspect background runs', keywords: ['jobs', 'background'], route: 'jobs' },
  { id: 'route-skills', group: 'navigate', kind: 'route', label: 'Open Skills', hint: 'Browse and manage skills', keywords: ['skills', 'hub'], route: 'skills' },
  { id: 'route-memory', group: 'navigate', kind: 'route', label: 'Open Memory', hint: 'Inspect long-term memory', keywords: ['memory', 'profile'], route: 'memory' },
  { id: 'route-missions', group: 'navigate', kind: 'route', label: 'Open Missions', hint: 'Track mission board items', keywords: ['missions', 'kanban'], route: 'missions' },
  { id: 'route-commands', group: 'navigate', kind: 'route', label: 'Open Command Browser', hint: 'Browse slash commands and parity badges', keywords: ['commands', 'palette', 'help'], route: 'commands' },

  { id: 'modal-settings', group: 'panels', kind: 'modal', label: 'Open Settings', hint: 'General settings and configuration', keywords: ['settings', 'config'], tab: 'settings' },
  { id: 'modal-tools', group: 'panels', kind: 'modal', label: 'Open Tools & Toolsets', hint: 'Inspect tools and toolsets', keywords: ['tools', 'toolsets'], tab: 'tools' },
  { id: 'modal-gateway', group: 'panels', kind: 'modal', label: 'Open Gateway Control Center', hint: 'Messaging gateway management', keywords: ['gateway', 'platforms'], tab: 'gateway' },
  { id: 'modal-skills', group: 'panels', kind: 'modal', label: 'Open Skills Control Center', hint: 'Skill management', keywords: ['skills'], tab: 'skills' },
  { id: 'modal-automations', group: 'panels', kind: 'modal', label: 'Open Automations', hint: 'Cron and scheduled jobs', keywords: ['automations', 'cron'], tab: 'automations' },
  { id: 'modal-insights', group: 'panels', kind: 'modal', label: 'Open Insights', hint: 'Analytics and insights', keywords: ['insights', 'analytics'], tab: 'insights' },

  { id: 'drawer-terminal', group: 'panels', kind: 'drawer', label: 'Open Terminal Drawer', hint: 'Terminal and workspace shell', keywords: ['terminal', 'shell'], tab: 'terminal' },
  { id: 'drawer-processes', group: 'panels', kind: 'drawer', label: 'Open Process Drawer', hint: 'Background process logs', keywords: ['process', 'logs'], tab: 'processes' },
  { id: 'drawer-logs', group: 'panels', kind: 'drawer', label: 'Open Logs Drawer', hint: 'Runtime logs and diagnostics', keywords: ['logs'], tab: 'logs' },
  { id: 'drawer-browser', group: 'panels', kind: 'drawer', label: 'Open Browser Drawer', hint: 'Browser control surface', keywords: ['browser'], tab: 'browser' },
];

const EXECUTE_DIRECTLY = new Set(['help', 'commands', 'status', 'usage', 'history', 'save', 'platforms', 'gateway', 'paste', 'restart', 'update', 'tools', 'toolsets', 'skills', 'browser', 'insights', 'cron', 'new']);
const PINNED_ACTIONS_STORAGE_KEY = 'hermes-command-palette-pinned';
const RECENT_ACTIONS_STORAGE_KEY = 'hermes-command-palette-recent';
const MAX_RECENT_ACTIONS = 8;
const MAX_PINNED_ACTIONS = 12;

const GROUP_LABELS: Record<PaletteAction['group'], string> = {
  pinned: 'Pinned',
  recent: 'Recent',
  navigate: 'Navigate',
  panels: 'Panels & Drawers',
  commands: 'Slash Commands',
};

function rankAction(action: PaletteAction, query: string): number {
  if (!query) return 0;
  const q = query.toLowerCase();
  const label = action.label.toLowerCase();
  if (label === q) return 100;
  if (label.startsWith(q)) return 80;
  if (action.keywords.some((keyword) => keyword.toLowerCase() === q)) return 70;
  if (action.keywords.some((keyword) => keyword.toLowerCase().startsWith(q))) return 60;
  if (label.includes(q)) return 40;
  if (action.hint.toLowerCase().includes(q)) return 20;
  return 0;
}

function fireAction(action: PaletteAction): void {
  if (action.kind === 'route') {
    window.dispatchEvent(new CustomEvent('hermes:navigate', { detail: { route: action.route } }));
    window.location.hash = `#/${action.route}`;
    return;
  }
  if (action.kind === 'modal') {
    window.dispatchEvent(new CustomEvent('hermes:openModal', { detail: { tab: action.tab } }));
    return;
  }
  if (action.kind === 'drawer') {
    window.dispatchEvent(new CustomEvent('hermes:openDrawer', { detail: { tab: action.tab } }));
    return;
  }
  if (action.kind === 'command') {
    if (action.executeDirectly) {
      window.dispatchEvent(new CustomEvent('hermes:executeCommand', { detail: { value: action.command.trim() } }));
    } else {
      window.dispatchEvent(new CustomEvent('hermes:prefillComposer', { detail: { value: action.command } }));
    }
    window.dispatchEvent(new CustomEvent('hermes:navigate', { detail: { route: 'chat' } }));
    window.location.hash = '#/chat';
  }
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const [commandActions, setCommandActions] = useState<PaletteAction[]>([]);
  const [pinnedActionIds, setPinnedActionIds] = useState<string[]>([]);
  const [recentActionIds, setRecentActionIds] = useState<string[]>([]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(PINNED_ACTIONS_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setPinnedActionIds(parsed.filter((item): item is string => typeof item === 'string').slice(0, MAX_PINNED_ACTIONS));
      }
    } catch {
      // ignore malformed localStorage
    }
  }, []);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(RECENT_ACTIONS_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        setRecentActionIds(parsed.filter((item): item is string => typeof item === 'string').slice(0, MAX_RECENT_ACTIONS));
      }
    } catch {
      // ignore malformed localStorage
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setSelected(0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let loadedCommands = false;
    apiClient.get<CommandsResponse>('/commands')
      .then((res) => {
        if (!res.ok || cancelled) return;
        const actions: PaletteAction[] = res.commands.slice(0, 40).map((command) => ({
          id: `command-${command.name}`,
          group: 'commands',
          kind: 'command',
          label: `Run /${command.name}`,
          hint: command.description,
          keywords: [command.name, ...command.aliases],
          command: `/${command.name}${command.name === 'help' || command.name === 'commands' ? '' : ' '}`,
          executeDirectly: EXECUTE_DIRECTLY.has(command.name),
        }));
        loadedCommands = actions.length > 0;
        setCommandActions(actions);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled && !loadedCommands) {
          setCommandActions([
            { id: 'command-help', group: 'commands', kind: 'command', label: 'Run /help', hint: 'Show available commands', keywords: ['help', 'commands'], command: '/help', executeDirectly: true },
          ]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const actions = useMemo(() => [...CORE_ACTIONS, ...commandActions], [commandActions]);
  const pinnedActions = useMemo(() => {
    if (query.trim()) return [] as PaletteAction[];
    return pinnedActionIds
      .map((id) => actions.find((action) => action.id === id))
      .filter((action): action is PaletteAction => Boolean(action))
      .map((action) => ({ ...action, group: 'pinned' as const }));
  }, [actions, pinnedActionIds, query]);
  const recentActions = useMemo(() => {
    if (query.trim()) return [] as PaletteAction[];
    return recentActionIds
      .map((id) => actions.find((action) => action.id === id))
      .filter((action): action is PaletteAction => Boolean(action))
      .map((action) => ({ ...action, group: 'recent' as const }));
  }, [actions, recentActionIds, query]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      return [
        ...pinnedActions,
        ...recentActions.filter((action) => !pinnedActionIds.includes(action.id)),
        ...actions.filter((action) => !pinnedActionIds.includes(action.id) && !recentActionIds.includes(action.id)),
      ];
    }
    return actions
      .map((action) => ({ action, score: rankAction(action, q) }))
      .filter(({ score }) => score > 0)
      .sort((a, b) => b.score - a.score || a.action.label.localeCompare(b.action.label))
      .map(({ action }) => action);
  }, [actions, pinnedActions, recentActions, pinnedActionIds, recentActionIds, query]);

  const grouped = useMemo(() => {
    const order: PaletteAction['group'][] = ['pinned', 'recent', 'navigate', 'panels', 'commands'];
    return order.map((group) => ({
      group,
      items: filtered.filter((action) => action.group === group),
    })).filter((section) => section.items.length > 0);
  }, [filtered]);

  useEffect(() => {
    setSelected(0);
  }, [query]);

  const rememberAction = (action: PaletteAction) => {
    setRecentActionIds((prev) => {
      const next = [action.id].concat(prev.filter((id) => id !== action.id)).slice(0, MAX_RECENT_ACTIONS);
      try {
        localStorage.setItem(RECENT_ACTIONS_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // ignore storage failures
      }
      return next;
    });
  };

  const togglePinnedAction = (action: PaletteAction) => {
    setPinnedActionIds((prev) => {
      const next = prev.includes(action.id)
        ? prev.filter((id) => id !== action.id)
        : [action.id].concat(prev).slice(0, MAX_PINNED_ACTIONS);
      try {
        localStorage.setItem(PINNED_ACTIONS_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // ignore storage failures
      }
      return next;
    });
  };

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(760px, 92vw)',
          maxHeight: '80vh',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: '24px',
          border: '1px solid rgba(255,255,255,0.08)',
          background: 'linear-gradient(180deg, rgba(15, 23, 42, 0.98), rgba(2, 6, 23, 0.98))',
          boxShadow: '0 32px 80px rgba(0,0,0,0.55)',
          overflow: 'hidden',
        }}
      >
        <div style={{ padding: '18px 20px 14px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ width: '40px', height: '40px', borderRadius: '14px', display: 'grid', placeItems: 'center', background: 'rgba(99,102,241,0.14)', color: '#a5b4fc', fontSize: '1.1rem' }}>⌘</div>
            <div style={{ flex: 1 }}>
              <div style={{ color: '#f8fafc', fontSize: '1rem', fontWeight: 700 }}>Command Palette</div>
              <div style={{ color: '#64748b', fontSize: '0.78rem' }}>Launch routes, control center tabs, drawers, or prefill slash commands.</div>
            </div>
            <button type="button" onClick={onClose} style={{ background: 'transparent', border: 'none', color: '#94a3b8', fontSize: '1.1rem', cursor: 'pointer' }}>✕</button>
          </div>
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                onClose();
                return;
              }
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setSelected((prev) => Math.min(prev + 1, filtered.length - 1));
                return;
              }
              if (e.key === 'ArrowUp') {
                e.preventDefault();
                setSelected((prev) => Math.max(prev - 1, 0));
                return;
              }
              if (e.key === 'Enter' && filtered[selected]) {
                e.preventDefault();
                fireAction(filtered[selected]);
                onClose();
              }
            }}
            placeholder="Search routes, actions, or commands…"
            style={{ marginTop: '14px', width: '100%', padding: '14px 16px', borderRadius: '16px', border: '1px solid rgba(99,102,241,0.2)', background: 'rgba(255,255,255,0.04)', color: '#f8fafc', fontSize: '0.95rem', outline: 'none' }}
          />
        </div>

        <div style={{ overflowY: 'auto', padding: '10px', display: 'grid', gap: '12px' }}>
          {grouped.map((section) => (
            <div key={section.group} style={{ display: 'grid', gap: '8px' }}>
              <div style={{ padding: '4px 10px', fontSize: '0.72rem', color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 700 }}>
                {GROUP_LABELS[section.group]}
              </div>
              {section.items.map((action) => {
                const index = filtered.findIndex((candidate) => candidate.id === action.id);
                const isPinned = pinnedActionIds.includes(action.id);
                return (
                  <div
                    key={action.id}
                    onClick={() => {
                      rememberAction(action);
                      fireAction(action);
                      onClose();
                    }}
                    onMouseEnter={() => setSelected(index)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        rememberAction(action);
                        fireAction(action);
                        onClose();
                      }
                    }}
                    role="button"
                    tabIndex={0}
                    aria-label={action.label}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'auto 1fr auto',
                      gap: '12px',
                      alignItems: 'center',
                      width: '100%',
                      textAlign: 'left',
                      padding: '12px 14px',
                      borderRadius: '16px',
                      border: selected === index ? '1px solid rgba(99,102,241,0.35)' : '1px solid transparent',
                      background: selected === index ? 'rgba(99,102,241,0.12)' : 'transparent',
                      color: '#e2e8f0',
                      cursor: 'pointer',
                    }}
                  >
                    <div style={{ width: '34px', height: '34px', borderRadius: '12px', display: 'grid', placeItems: 'center', background: 'rgba(255,255,255,0.06)', color: '#a5b4fc', fontWeight: 700 }}>
                      {action.kind === 'route' ? '↗' : action.kind === 'modal' ? '◫' : action.kind === 'drawer' ? '▤' : action.executeDirectly ? '⚡' : '/'}
                    </div>
                    <div>
                      <div style={{ fontSize: '0.92rem', fontWeight: 600 }}>{action.label}</div>
                      <div style={{ fontSize: '0.78rem', color: '#64748b', marginTop: '3px' }}>{action.hint}</div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '6px' }}>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          togglePinnedAction(action);
                        }}
                        title={isPinned ? 'Unpin action' : 'Pin action'}
                        style={{
                          background: isPinned ? 'rgba(251,191,36,0.14)' : 'rgba(255,255,255,0.04)',
                          color: isPinned ? '#fde68a' : '#94a3b8',
                          border: `1px solid ${isPinned ? 'rgba(251,191,36,0.25)' : 'rgba(255,255,255,0.08)'}`,
                          borderRadius: '999px',
                          padding: '4px 8px',
                          fontSize: '0.72rem',
                          cursor: 'pointer',
                        }}
                      >
                        {isPinned ? '★ pinned' : '☆ pin'}
                      </button>
                      <div style={{ fontSize: '0.72rem', color: '#94a3b8', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '999px', padding: '4px 8px' }}>{action.kind}</div>
                      {action.kind === 'command' && (
                        <div style={{ fontSize: '0.66rem', color: action.executeDirectly ? '#86efac' : '#a5b4fc' }}>
                          {action.executeDirectly ? 'executes now' : 'prefills chat'}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
          {filtered.length === 0 && (
            <div style={{ padding: '24px', textAlign: 'center', color: '#94a3b8' }}>No actions matched your search.</div>
          )}
        </div>
        <div style={{ padding: '10px 16px', borderTop: '1px solid rgba(255,255,255,0.06)', display: 'flex', justifyContent: 'space-between', gap: '12px', color: '#64748b', fontSize: '0.74rem' }}>
          <span>↑↓ move · Enter launch · Esc close</span>
          <span>Ctrl/Cmd+K to reopen</span>
        </div>
      </div>
    </div>
  );
}