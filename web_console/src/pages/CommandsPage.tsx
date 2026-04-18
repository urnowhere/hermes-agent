import { useEffect, useMemo, useState } from 'react';
import { apiClient } from '../lib/api';
import { LoadingSpinner } from '../components/shared/LoadingSpinner';
import { EmptyState } from '../components/shared/EmptyState';

interface CommandEntry {
  name: string;
  description: string;
  category: string;
  aliases: string[];
  names: string[];
  args_hint: string;
  subcommands: string[];
  cli_only: boolean;
  gateway_only: boolean;
  gateway_config_gate?: string | null;
}

interface CommandsResponse {
  ok: boolean;
  commands: CommandEntry[];
}

type ParityStatus = 'full' | 'partial' | 'cli_only';

const FULLY_SUPPORTED = new Set([
  'new', 'retry', 'undo', 'title', 'branch', 'fork', 'compress', 'rollback', 'stop',
  'approve', 'deny', 'background', 'btw', 'queue', 'status', 'profile', 'resume',
  'model', 'provider', 'personality', 'verbose', 'yolo', 'reasoning',
  'fast', 'skin', 'tools', 'toolsets', 'skills', 'cron', 'reload', 'reload-mcp', 'reload_mcp',
  'browser', 'commands', 'help', 'usage', 'insights', 'save', 'image', 'paste',
  'reset', 'bg', 'snapshot', 'snap', 'statusbar', 'sb'
]);

const PARTIALLY_SUPPORTED = new Set([
  'clear', 'quit', 'exit', 'q',
  'config', 'history', 'platforms', 'gateway', 'sethome', 'set-home',
  'voice', 'update', 'restart', 'debug'
]);

function parityFor(command: CommandEntry): ParityStatus {
  if (command.cli_only && !command.gateway_config_gate) return 'cli_only';
  if (FULLY_SUPPORTED.has(command.name) || command.aliases.some((alias) => FULLY_SUPPORTED.has(alias))) return 'full';
  if (PARTIALLY_SUPPORTED.has(command.name) || command.aliases.some((alias) => PARTIALLY_SUPPORTED.has(alias))) return 'partial';
  return 'partial';
}

const BADGE_STYLE: Record<ParityStatus, { label: string; bg: string; color: string; border: string }> = {
  full: { label: 'Full', bg: 'rgba(74, 222, 128, 0.12)', color: '#86efac', border: 'rgba(74, 222, 128, 0.25)' },
  partial: { label: 'Partial', bg: 'rgba(251, 191, 36, 0.12)', color: '#fde68a', border: 'rgba(251, 191, 36, 0.25)' },
  cli_only: { label: 'CLI only', bg: 'rgba(148, 163, 184, 0.12)', color: '#cbd5e1', border: 'rgba(148, 163, 184, 0.25)' },
};

export function CommandsPage() {
  const [commands, setCommands] = useState<CommandEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');

  useEffect(() => {
    apiClient.get<CommandsResponse>('/commands')
      .then((res) => {
        if (res.ok) setCommands(res.commands);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((command) =>
      command.name.toLowerCase().includes(q)
      || command.description.toLowerCase().includes(q)
      || command.category.toLowerCase().includes(q)
      || command.aliases.some((alias) => alias.toLowerCase().includes(q))
    );
  }, [commands, query]);

  if (loading) return <LoadingSpinner message="Loading command registry…" />;
  if (!commands.length) return <EmptyState icon="⌘" title="No commands found" description="The shared Hermes command registry is unavailable." />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flex: 1, overflowY: 'auto', minHeight: 0, paddingBottom: '30px', paddingRight: '12px' }}>
      <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '16px', padding: '16px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '16px' }}>
        <div>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>⌘ Command Browser</h2>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>
            Shared slash-command registry from Hermes CLI, annotated with web-console parity.
          </p>
        </div>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter commands…"
          style={{ padding: '8px 14px', borderRadius: '10px', width: '240px', border: '1px solid rgba(129,140,248,0.2)', background: 'rgba(0,0,0,0.2)', color: 'white', fontSize: '0.85rem' }}
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px' }}>
        {filtered.map((command) => {
          const parity = parityFor(command);
          const badge = BADGE_STYLE[parity];
          return (
            <section key={command.name} style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '16px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}>
                <div>
                  <div style={{ color: '#f8fafc', fontSize: '1rem', fontWeight: 700, fontFamily: 'monospace' }}>/ {command.name}</div>
                  <div style={{ color: '#64748b', fontSize: '0.78rem', marginTop: '2px' }}>{command.category}</div>
                </div>
                <span style={{ background: badge.bg, color: badge.color, border: `1px solid ${badge.border}`, borderRadius: '999px', padding: '4px 10px', fontSize: '0.75rem', fontWeight: 700 }}>{badge.label}</span>
              </div>

              <div style={{ color: '#cbd5e1', fontSize: '0.9rem', lineHeight: 1.45 }}>{command.description}</div>

              {command.args_hint && (
                <div style={{ color: '#94a3b8', fontSize: '0.8rem' }}>
                  Usage: <code style={{ color: '#a5b4fc' }}>/{command.name} {command.args_hint}</code>
                </div>
              )}

              {command.aliases.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {command.aliases.map((alias) => (
                    <span key={alias} style={{ background: 'rgba(99,102,241,0.12)', color: '#a5b4fc', border: '1px solid rgba(99,102,241,0.2)', borderRadius: '999px', padding: '3px 8px', fontSize: '0.75rem', fontFamily: 'monospace' }}>
                      /{alias}
                    </span>
                  ))}
                </div>
              )}

              {command.subcommands.length > 0 && (
                <div style={{ color: '#94a3b8', fontSize: '0.78rem' }}>
                  Subcommands: {command.subcommands.join(', ')}
                </div>
              )}

              {(command.cli_only || command.gateway_only || command.gateway_config_gate) && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                  {command.cli_only && <span style={{ color: '#cbd5e1', fontSize: '0.72rem', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '999px', padding: '3px 8px' }}>CLI-only source</span>}
                  {command.gateway_only && <span style={{ color: '#93c5fd', fontSize: '0.72rem', border: '1px solid rgba(59,130,246,0.2)', borderRadius: '999px', padding: '3px 8px' }}>Gateway command</span>}
                  {command.gateway_config_gate && <span style={{ color: '#fcd34d', fontSize: '0.72rem', border: '1px solid rgba(251,191,36,0.2)', borderRadius: '999px', padding: '3px 8px' }}>Gated by {command.gateway_config_gate}</span>}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}