export type PrimaryRoute = 'chat' | 'sessions' | 'workspace' | 'usage' | 'jobs' | 'skills' | 'memory' | 'missions' | 'commands';

export type InspectorTab = 'run' | 'tools' | 'todo' | 'session' | 'human' | 'memory' | 'logs' | 'browser';
export type DrawerTab = 'terminal' | 'processes' | 'logs' | 'browser';
export type ModalTab = 'settings' | 'tools' | 'gateway' | 'skills' | 'automations' | 'insights';

export interface NavItem {
  id: PrimaryRoute;
  label: string;
  description: string;
}

export interface RouteDefinition extends NavItem {
  headline: string;
  summary: string;
}

export interface UiState {
  route: PrimaryRoute;
  inspectorOpen: boolean;
  inspectorTab: InspectorTab;
  drawerOpen: boolean;
  drawerTab: DrawerTab;
  modalOpen: boolean;
  modalTab: ModalTab;
  voiceMode: boolean;
  artifactOpen: boolean;
  artifactContent: string | null;
  artifactType: string | null;
}

export interface SessionSummary {
  sessionId: string;
  title: string;
  lastActive?: string;
}

export interface RunSummary {
  status: 'idle' | 'running' | 'completed' | 'failed';
  currentTool?: string;
}

export interface WorkspaceSummary {
  root: string;
  selectedPath?: string;
}
