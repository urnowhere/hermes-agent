import type { RouteDefinition } from '../lib/types';

export const ROUTES: readonly RouteDefinition[] = [
  {
    id: 'chat',
    label: 'Chat',
    description: 'Talk to Hermes and watch tool activity.',
    headline: 'Chat with Hermes',
    summary: 'Streaming chat, tool events, approvals, and session-aware interaction will live here.'
  },
  {
    id: 'sessions',
    label: 'Sessions',
    description: 'Browse, inspect, and resume past work.',
    headline: 'Session browser',
    summary: 'Searchable history, transcript previews, exports, and resume actions belong on this page.'
  },
  {
    id: 'workspace',
    label: 'Workspace',
    description: 'Inspect files, diffs, and processes.',
    headline: 'Workspace operations',
    summary: 'File trees, patches, checkpoints, rollback, and process logs will be surfaced here.'
  },
  {
    id: 'usage',
    label: 'Usage',
    description: 'Analytics, token usage, and costs.',
    headline: 'Usage Insights',
    summary: 'Track your Hermes API usage.'
  },
  {
    id: 'jobs',
    label: 'Background Jobs',
    description: 'Track background agents.',
    headline: 'Background Jobs',
    summary: 'Monitor status of dispatched background agents.'
  },
  {
    id: 'skills',
    label: 'Skills',
    description: 'Browse, install, and manage skills.',
    headline: 'Skills Hub',
    summary: 'Enhance your agent with official and community skills or create your own.'
  },
  {
    id: 'memory',
    label: 'Memory',
    description: 'View and edit long-term agent memory.',
    headline: 'Agent Memory',
    summary: 'What Hermes remembers about you and your workspace across sessions.'
  },
  {
    id: 'missions',
    label: 'Missions',
    description: 'Kanban board for agent tasks.',
    headline: 'Missions Board',
    summary: 'Organize and track agent missions with drag-and-drop Kanban columns.'
  },
  {
    id: 'commands',
    label: 'Commands',
    description: 'Browse shared Hermes slash commands and parity status.',
    headline: 'Command Browser',
    summary: 'See the canonical command registry, aliases, usage hints, and which commands are fully or partially supported in the web console.'
  }
] as const;

export const PRIMARY_NAV_ITEMS = ROUTES.map((item) => item.label);
