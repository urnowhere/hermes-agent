import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from './App';
import { PRIMARY_NAV_ITEMS } from './router';

class MockEventSource {
  url: string;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.lastInstance = this;
  }

  close() {
    // no-op
  }

  static lastInstance: MockEventSource | null = null;

  simulateMessage(data: unknown) {
    const payload = { data: JSON.stringify(data) } as MessageEvent;
    this.onmessage?.(payload);
  }
}

describe('App shell', () => {
  beforeEach(() => {
    global.EventSource = MockEventSource as unknown as typeof EventSource;
    MockEventSource.lastInstance = null;
    window.location.hash = '';
    localStorage.clear();

    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (!(URL as any).createObjectURL) {
        (URL as any).createObjectURL = vi.fn(() => 'blob:mock');
      }
      if (!(URL as any).revokeObjectURL) {
        (URL as any).revokeObjectURL = vi.fn();
      }
      if (url.includes('/api/gui/human/pending')) {
        return new Response(JSON.stringify({ ok: true, pending: [] }), { status: 200 });
      }
      if (url.includes('/api/gui/chat/send')) {
        return new Response(
          JSON.stringify({ ok: true, session_id: 'session-live', run_id: 'run-1', status: 'started' }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/user-profile')) {
        return new Response(
          JSON.stringify({ ok: true, user_profile: { target: 'user', enabled: true, entries: ['Likes dark mode.'], entry_count: 1, usage: { text: '1%', percent: 1, current_chars: 16, char_limit: 1375 }, path: '/tmp/USER.md' } }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/memory')) {
        return new Response(
          JSON.stringify({ ok: true, memory: { target: 'memory', enabled: true, entries: ['Test memory entry.'], entry_count: 1, usage: { text: '1%', percent: 1, current_chars: 18, char_limit: 2200 }, path: '/tmp/MEMORY.md' } }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/session-search')) {
        return new Response(JSON.stringify({ ok: true, results: [] }), { status: 200 });
      }
      if (url.includes('/api/gui/skills')) {
        return new Response(
          JSON.stringify({ ok: true, skills: [{ name: 'writing-plans', description: 'Write plans.', source_type: 'builtin' }] }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/cron/jobs')) {
        return new Response(
          JSON.stringify({ ok: true, jobs: [{ job_id: 'cron-1', name: 'Morning summary', schedule: '0 9 * * *', paused: false }] }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/chat/backgrounds')) {
        return new Response(
          JSON.stringify({ ok: true, background_runs: [{ run_id: 'run-1', session_id: 'sess-1', status: 'running', prompt: 'Analyze log files', created_at: Date.now() / 1000 }] }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/workspace/tree')) {
        return new Response(
          JSON.stringify({
            ok: true,
            tree: {
              name: 'workspace',
              path: '.',
              type: 'directory',
              children: [{ name: 'src/app.py', path: 'src/app.py', type: 'file' }]
            }
          }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/workspace/file')) {
        return new Response(JSON.stringify({ ok: true, path: 'src/app.py', content: 'def main():\n    return 1\n' }), { status: 200 });
      }
      if (url.includes('/api/gui/workspace/diff')) {
        return new Response(JSON.stringify({ ok: true, diff: '--- a\n+++ b\n@@\n-old\n+new' }), { status: 200 });
      }
      if (url.includes('/api/gui/workspace/checkpoints')) {
        return new Response(JSON.stringify({ ok: true, checkpoints: [{ checkpoint_id: 'cp-1', label: 'before patch' }] }), { status: 200 });
      }
      if (url.includes('/api/gui/processes')) {
        return new Response(JSON.stringify({ ok: true, processes: [{ process_id: 'proc-1', status: 'running' }] }), { status: 200 });
      }
      if (url.includes('/api/gui/gateway/platforms')) {
        return new Response(
          JSON.stringify({ ok: true, platforms: [{ key: 'telegram', label: 'Telegram', runtime_state: 'connected', enabled: true, configured: true }] }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/gateway/pairing')) {
        return new Response(JSON.stringify({ ok: true, pairings: [] }), { status: 200 });
      }
      if (url.includes('/api/gui/gateway/overview')) {
        return new Response(JSON.stringify({ ok: true, overview: { summary: { platform_count: 5, connected_platforms: 2, enabled_platforms: 3 } } }), { status: 200 });
      }
      if (url.includes('/api/gui/settings')) {
        return new Response(
          JSON.stringify({ ok: true, settings: { model: 'hermes-agent', provider: 'openai-codex', browser_mode: 'local', tts_provider: 'edge' } }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/logs')) {
        return new Response(JSON.stringify({ ok: true, lines: ['[info] hello', '[info] world'] }), { status: 200 });
      }
      if (url.includes('/api/gui/sessions/') && url.endsWith('/transcript')) {
        return new Response(
          JSON.stringify({ ok: true, items: [{ role: 'user', content: 'hello' }, { role: 'assistant', content: 'hi' }] }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/sessions/')) {
        return new Response(JSON.stringify({ ok: true, session: { title: 'Session One', recap: { preview: 'Loaded from API' } } }), {
          status: 200
        });
      }
      if (url.includes('/api/gui/sessions')) {
        return new Response(
          JSON.stringify({ ok: true, sessions: [{ session_id: 'sess-1', title: 'Session One', source: 'cli', last_active: 123 }] }),
          { status: 200 }
        );
      }
      if (url.includes('/api/gui/commands')) {
        return new Response(JSON.stringify({ ok: true, commands: [
          { name: 'help', description: 'Show available commands', category: 'Info', aliases: [], names: ['help'], args_hint: '', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'model', description: 'Switch model for this session', category: 'Configuration', aliases: [], names: ['model'], args_hint: '[model]', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'queue', description: 'Queue a prompt for the next turn', category: 'Session', aliases: ['q'], names: ['queue', 'q'], args_hint: '<prompt>', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'snapshot', description: 'Create or restore state snapshots of Hermes config/state', category: 'Session', aliases: ['snap'], names: ['snapshot', 'snap'], args_hint: '[create|restore <id>|prune]', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'reload', description: 'Reload .env variables into the running session', category: 'Tools & Skills', aliases: [], names: ['reload'], args_hint: '', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'reload-mcp', description: 'Reload MCP servers from config', category: 'Tools & Skills', aliases: ['reload_mcp'], names: ['reload-mcp', 'reload_mcp'], args_hint: '', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'title', description: 'Set a title for the current session', category: 'Session', aliases: [], names: ['title'], args_hint: '<title>', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'rollback', description: 'List or restore filesystem checkpoints', category: 'Session', aliases: [], names: ['rollback'], args_hint: '[checkpoint_id]', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'branch', description: 'Branch the current session', category: 'Session', aliases: ['fork'], names: ['branch', 'fork'], args_hint: '[name]', subcommands: [], cli_only: false, gateway_only: false },
          { name: 'debug', description: 'Upload debug report and get shareable links', category: 'Info', aliases: [], names: ['debug'], args_hint: '[share|local]', subcommands: [], cli_only: false, gateway_only: false }
        ] }), { status: 200 });
      }
      if (url.includes('/api/gui/system/snapshots') && url.includes('limit=')) {
        return new Response(JSON.stringify({ ok: true, snapshots: [
          { id: 'snap-2', label: 'before-upgrade', file_count: 4, total_size: 2048 },
        ] }), { status: 200 });
      }
      if (url.endsWith('/api/gui/system/snapshots')) {
        return new Response(JSON.stringify({ ok: true, snapshot_id: 'snap-3', snapshot: { id: 'snap-3', label: 'pre-upgrade', file_count: 5, total_size: 4096 } }), { status: 200 });
      }
      if (url.includes('/api/gui/system/snapshots/restore')) {
        return new Response(JSON.stringify({ ok: true, snapshot_id: 'snap-2', message: 'Restored snapshot snap-2. Restart recommended for state.db changes to take effect.' }), { status: 200 });
      }
      if (url.includes('/api/gui/system/snapshots/prune')) {
        return new Response(JSON.stringify({ ok: true, deleted: 3, keep: 5, message: 'Pruned 3 old snapshot(s) (keeping 5).' }), { status: 200 });
      }
      if (url.includes('/api/gui/system/reload')) {
        return new Response(JSON.stringify({ ok: true, updated: 2, message: 'Reloaded .env (2 var(s) updated)' }), { status: 200 });
      }
      if (url.includes('/api/gui/system/debug')) {
        return new Response(JSON.stringify({ ok: true, mode: 'upload', report_url: 'https://paste.test/report', agent_log_url: 'https://paste.test/agent', gateway_log_url: null, failures: [] }), { status: 200 });
      }
      if (url.includes('/api/gui/mcp/reload')) {
        return new Response(JSON.stringify({ ok: true, reloaded: true }), { status: 200 });
      }
      if (url.includes('/api/gui/system/backup')) {
        return new Response('backup-bytes', {
          status: 200,
          headers: {
            'Content-Disposition': 'attachment; filename="hermes-backup-test.zip"',
            'X-Backup-Files': '7',
          },
        });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as typeof fetch;
  });

  it('renders the primary navigation items', () => {
    render(<App />);

    const nav = screen.getByRole('navigation', { name: /Primary navigation/i });
    for (const item of PRIMARY_NAV_ITEMS) {
      expect(within(nav).getByRole('button', { name: new RegExp(item, 'i') })).toBeInTheDocument();
    }
  });

  it('renders the chat page by default', () => {
    render(<App />);

    expect(screen.getByLabelText('Transcript')).toBeInTheDocument();
    expect(screen.getByLabelText('Composer')).toBeInTheDocument();
  });

  it('switches route content when navigating to Sessions', async () => {
    render(<App />);

    const nav = screen.getByRole('navigation', { name: /Primary navigation/i });
    fireEvent.click(within(nav).getByRole('button', { name: /Sessions/i }));
    
    expect((await screen.findAllByText('Session One')).length).toBeGreaterThan(0);
  });

  it('switches route content when navigating to Workspace', async () => {
    render(<App />);

    const nav = screen.getByRole('navigation', { name: /Primary navigation/i });
    fireEvent.click(within(nav).getByRole('button', { name: /Workspace/i }));
    
    expect(await screen.findByLabelText('File tree')).toBeInTheDocument();
    expect(screen.getByLabelText('Terminal panel')).toBeInTheDocument();
    expect(screen.getByLabelText('Process panel')).toBeInTheDocument();
  });

  it('switches route content when navigating to Memory, Skills, and Automations', async () => {
    render(<App />);
    const nav = screen.getByRole('navigation', { name: /Primary navigation/i });

    fireEvent.click(within(nav).getByRole('button', { name: /Memory/i }));
    expect(await screen.findByText('Test memory entry.')).toBeInTheDocument();

    fireEvent.click(within(nav).getByRole('button', { name: /Skills/i }));

    const installedTab = await screen.findByRole('button', { name: /Installed & Local/i });
    fireEvent.click(installedTab);

    expect(await screen.findByText(/writing-plans/i)).toBeInTheDocument();

    fireEvent.click(within(nav).getByRole('button', { name: /Background Jobs/i }));
    expect(await screen.findByText(/Analyze log files/i)).toBeInTheDocument();
  });

  it('switches modal tabs when navigating inside Control Center', async () => {
    render(<App />);

    // Open Control Center first via title
    fireEvent.click(screen.getByTitle('Control Center'));

    // By default Settings form should be visible
    expect(await screen.findByLabelText('Settings form')).toBeInTheDocument();

    // Switch to Gateway tab
    fireEvent.click(screen.getByRole('button', { name: /Messaging Gateway/i }));
    expect(await screen.findByText(/Gateway Platforms/i)).toBeInTheDocument();

    // Switch to Automations tab
    fireEvent.click(screen.getByRole('button', { name: /Automations/i }));
    expect(await screen.findByText(/Morning summary/i)).toBeInTheDocument();
  });

  it('opens SSE after sending a message and receives streaming events into transcript', async () => {
    render(<App />);

    // SSE is not created on mount; it opens after a send that returns a real session id.
    const prompt = screen.getByPlaceholderText(/Message Hermes.../i);
    await act(async () => {
      fireEvent.change(prompt, { target: { value: 'Hello from test' } });
      fireEvent.submit(screen.getByLabelText('Composer'));
    });

    // Wait for send to complete (Hermes is thinking indicator appears after POST resolves)
    await waitFor(() => {
      expect(screen.getByText(/Hermes is thinking/i)).toBeInTheDocument();
    });

    // SSE should now be open
    const es = MockEventSource.lastInstance;
    expect(es).not.toBeNull();
    expect(es!.url).toContain('/api/gui/stream/session/session-live');

    await act(async () => {
      es!.simulateMessage({
        type: 'tool.started',
        session_id: 'session-live',
        run_id: 'run-1',
        payload: { tool_name: 'search_files', preview: 'search_files(pattern=*.py)' },
        ts: Date.now() / 1000
      });
    });

    await waitFor(() => {
      expect(screen.getByText(/search_files\(pattern=\*\.py\)/)).toBeInTheDocument();
    });

    await act(async () => {
      es!.simulateMessage({
        type: 'message.assistant.completed',
        session_id: 'session-live',
        run_id: 'run-1',
        payload: { content: 'Hermes completed analysis.' },
        ts: Date.now() / 1000
      });
    });
  });

  it('toggles the inspector and drawer from the top bar', () => {
    render(<App />);

    const inspector = screen.getByLabelText('Inspector');
    const drawer = screen.getByLabelText('Bottom drawer');

    fireEvent.click(screen.getByTitle('Inspector Panel'));
    expect(inspector.className).toContain('inspector-hidden');

    fireEvent.click(screen.getByTitle('Terminal Drawer'));
    expect(drawer.className).not.toContain('bottom-drawer-hidden');
  });

  it('changes inspector tabs', () => {
    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'tools' }));
    expect(screen.getByRole('button', { name: 'tools' }).className).toContain('panel-tab-active');
  });

  it('runs richer /snapshot subcommands in chat', async () => {
    render(<App />);

    const prompt = screen.getByPlaceholderText(/Message Hermes.../i);
    const composer = screen.getByLabelText('Composer');

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/snapshot' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/system/snapshots?limit=20'))).toBe(true);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/snapshot create pre-upgrade' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).endsWith('/api/gui/system/snapshots'))).toBe(true);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/snapshot restore snap-2' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/system/snapshots/restore'))).toBe(true);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/snapshot prune 5' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/system/snapshots/prune'))).toBe(true);
    });
  });

  it('runs /reload and /debug slash commands in chat', async () => {
    render(<App />);

    const prompt = screen.getByPlaceholderText(/Message Hermes.../i);
    const composer = screen.getByLabelText('Composer');

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/reload' } });
      fireEvent.submit(composer);
    });

    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/system/reload'))).toBe(true);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/debug' } });
      fireEvent.submit(composer);
    });

    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/system/debug'))).toBe(true);
    });
  });

  it('runs /title, /rollback, /fork, and /reload_mcp slash commands in chat', async () => {
    render(<App />);

    const prompt = screen.getByPlaceholderText(/Message Hermes.../i);
    const composer = screen.getByLabelText('Composer');

    await act(async () => {
      fireEvent.change(prompt, { target: { value: 'Hello from test' } });
      fireEvent.submit(composer);
    });

    await waitFor(() => {
      expect(screen.getByText(/Hermes is thinking/i)).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/title Renamed Session' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/sessions/session-live/title'))).toBe(true);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/rollback' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.filter((call: any[]) => String(call[0]).includes('/api/gui/workspace/checkpoints')).length).toBeGreaterThan(0);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/rollback cp-1' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/workspace/rollback'))).toBe(true);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/fork Experimental branch' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/sessions/session-live/branch'))).toBe(true);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/reload_mcp' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect((global.fetch as any).mock.calls.some((call: any[]) => String(call[0]).includes('/api/gui/mcp/reload'))).toBe(true);
    });
  });

  it('runs /statusbar, /sb, /quit, /exit, and /q slash commands in chat', async () => {
    render(<App />);

    const prompt = screen.getByPlaceholderText(/Message Hermes.../i);
    const composer = screen.getByLabelText('Composer');

    expect(screen.getByLabelText('Run status')).toBeInTheDocument();

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/statusbar off' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect(screen.queryByLabelText('Run status')).not.toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/sb on' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect(screen.getByLabelText('Run status')).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/quit' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect(screen.getByText(/close the browser tab/i)).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/exit' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect(screen.getAllByText(/close the browser tab/i).length).toBeGreaterThan(0);
    });

    await act(async () => {
      fireEvent.change(prompt, { target: { value: '/q queued from alias' } });
      fireEvent.submit(composer);
    });
    await waitFor(() => {
      expect(screen.getByText(/No active run — sending queued prompt immediately/i)).toBeInTheDocument();
    });
  });

  it('opens the command palette and prefills slash commands into chat', async () => {
    render(<App />);

    fireEvent.click(screen.getByTitle(/Command Palette/i));
    expect(await screen.findByText(/Command Palette/i)).toBeInTheDocument();

    const search = screen.getByPlaceholderText(/Search routes, actions, or commands/i);
    fireEvent.change(search, { target: { value: 'model' } });

    const runModelButton = screen.getAllByRole('button').find((button) => button.textContent?.includes('Run /model'));
    expect(runModelButton).toBeTruthy();
    fireEvent.click(runModelButton as HTMLButtonElement);

    await waitFor(() => {
      expect(screen.getByLabelText('Composer')).toBeInTheDocument();
      const textarea = document.querySelector('#chat-prompt') as HTMLTextAreaElement | null;
      expect(textarea?.value).toBe('/model ');
    });
  });

  it('can pin actions from the command palette and show them in the pinned section', async () => {
    render(<App />);

    fireEvent.click(screen.getByTitle(/Command Palette/i));
    expect(await screen.findByText(/Command Palette/i)).toBeInTheDocument();

    const openUsageLabel = await screen.findByText(/Open Usage/i);
    const openUsage = openUsageLabel.closest('[role="button"]') as HTMLElement;
    const pinButton = within(openUsage).getByRole('button', { name: /☆ pin/i });
    fireEvent.click(pinButton);

    fireEvent.click(screen.getAllByRole('button', { name: '✕' })[0]);
    fireEvent.click(screen.getByTitle(/Command Palette/i));

    expect(await screen.findByText('Pinned')).toBeInTheDocument();
    const pinnedHeading = screen.getByText('Pinned');
    const pinnedSection = pinnedHeading.parentElement as HTMLElement;
    expect(within(pinnedSection).getByText(/Open Usage/i)).toBeInTheDocument();
  });
});
