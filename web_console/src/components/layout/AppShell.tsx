import { useEffect, useMemo, useState } from 'react';
import { ROUTES } from '../../app/router';
import type { DrawerTab, InspectorTab, ModalTab, PrimaryRoute } from '../../lib/types';
import { useConnection, HERMES_ONLY_ROUTES } from '../../lib/connectionContext';
import { ChatPage } from '../../pages/ChatPage';
import { SessionsPage } from '../../pages/SessionsPage';
import { WorkspacePage } from '../../pages/WorkspacePage';
import { UsagePage } from '../../pages/UsagePage';
import { JobsPage } from '../../pages/JobsPage';
import { SkillsPage } from '../../pages/SkillsPage';
import { MemoryPage } from '../../pages/MemoryPage';
import { MissionsPage } from '../../pages/MissionsPage';
import { CommandsPage } from '../../pages/CommandsPage';
import { initialUiState, setInspectorTab, setRoute, toggleDrawer, toggleInspector, toggleModal, toggleVoiceMode } from '../../store/uiStore';
import { BottomDrawer } from './BottomDrawer';
import { Inspector } from './Inspector';
import { TopBar } from './TopBar';
import { ControlCenterModal } from './ControlCenterModal';
import { CommandPalette } from './CommandPalette';
import { Toaster } from '../shared/Toaster';
import { ArtifactViewer } from '../chat/ArtifactViewer';

/** Render all routes simultaneously, hiding inactive ones with CSS.
 *  This keeps components mounted so chat state, SSE connections, etc. persist
 *  across tab switches instead of being destroyed on unmount. */
function AllRoutes({ activeRoute, voiceMode }: { activeRoute: PrimaryRoute; voiceMode: boolean }) {
  const hidden = (id: PrimaryRoute) => ({ display: activeRoute === id ? 'flex' : 'none', flex: 1, flexDirection: 'column' as const, overflow: 'hidden' });
  return (
    <>
      <div style={hidden('chat')}><ChatPage voiceMode={voiceMode} /></div>
      <div style={hidden('sessions')}><SessionsPage /></div>
      <div style={hidden('workspace')}><WorkspacePage /></div>
      <div style={hidden('usage')}><UsagePage /></div>
      <div style={hidden('jobs')}><JobsPage /></div>
      <div style={hidden('skills')}><SkillsPage /></div>
      <div style={hidden('memory')}><MemoryPage /></div>
      <div style={hidden('missions')}><MissionsPage /></div>
      <div style={hidden('commands')}><CommandsPage /></div>
    </>
  );
}

export function AppShell() {
  const [uiState, setUiState] = useState(initialUiState);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const connection = useConnection();
  const route = useMemo(() => ROUTES.find((item) => item.id === uiState.route) ?? ROUTES[0], [uiState.route]);

  const navigate = (nextRoute: PrimaryRoute) => {
    // Block navigation to Hermes-only routes when disconnected
    if (!connection.online && HERMES_ONLY_ROUTES.has(nextRoute)) return;
    setUiState((current) => setRoute(current, nextRoute));
  };
  const selectInspectorTab = (tab: InspectorTab) => setUiState((current) => setInspectorTab(current, tab));
  const selectDrawerTab = (tab: DrawerTab) => setUiState((current) => toggleDrawer(current, tab));
  const selectModalTab = (tab: ModalTab) => setUiState((current) => toggleModal(current, tab));

  // Hash Routing Deep Linking
  useEffect(() => {
    const handleHashChange = () => {
      const hash = window.location.hash.replace('#/', '');
      const parts = hash.split('/');
      const nextRoute = parts[0] as PrimaryRoute;
      
      if (['chat', 'sessions', 'workspace', 'usage', 'jobs', 'skills', 'memory', 'missions', 'commands'].includes(nextRoute)) {
        if (uiState.route !== nextRoute) {
          setUiState(current => setRoute(current, nextRoute));
        }
      }
    };
    
    if (window.location.hash) {
      handleHashChange();
    }
    
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  useEffect(() => {
    const handleOpenArtifact = (e: Event) => {
      const customEvent = e as CustomEvent<{ type: string; content: string }>;
      setUiState(current => ({
        ...current,
        artifactOpen: true,
        artifactType: customEvent.detail.type,
        artifactContent: customEvent.detail.content
      }));
    };

    const handleNavigate = (e: Event) => {
      const customEvent = e as CustomEvent<{ route?: PrimaryRoute }>;
      const nextRoute = customEvent.detail?.route;
      if (!nextRoute) return;
      navigate(nextRoute);
    };

    const handleOpenModal = (e: Event) => {
      const customEvent = e as CustomEvent<{ tab?: ModalTab }>;
      const tab = customEvent.detail?.tab;
      if (!tab) return;
      setUiState((current) => toggleModal(current, tab));
    };

    const handleOpenDrawer = (e: Event) => {
      const customEvent = e as CustomEvent<{ tab?: DrawerTab }>;
      const tab = customEvent.detail?.tab;
      if (!tab) return;
      setUiState((current) => toggleDrawer(current, tab));
    };

    const handleToggleVoice = () => {
      setUiState((current) => toggleVoiceMode(current));
    };

    const handleOpenPalette = () => {
      setPaletteOpen(true);
    };

    window.addEventListener('hermes:openArtifact', handleOpenArtifact);
    window.addEventListener('hermes:navigate', handleNavigate);
    window.addEventListener('hermes:openModal', handleOpenModal);
    window.addEventListener('hermes:openDrawer', handleOpenDrawer);
    window.addEventListener('hermes:toggleVoiceMode', handleToggleVoice);
    window.addEventListener('hermes:openCommandPalette', handleOpenPalette);
    return () => {
      window.removeEventListener('hermes:openArtifact', handleOpenArtifact);
      window.removeEventListener('hermes:navigate', handleNavigate);
      window.removeEventListener('hermes:openModal', handleOpenModal);
      window.removeEventListener('hermes:openDrawer', handleOpenDrawer);
      window.removeEventListener('hermes:toggleVoiceMode', handleToggleVoice);
      window.removeEventListener('hermes:openCommandPalette', handleOpenPalette);
    };
  }, []);

  // Sync hash to state changes
  useEffect(() => {
    const currentHashRoute = window.location.hash.replace('#/', '').split('/')[0];
    if (currentHashRoute !== uiState.route) {
      const remainingHash = window.location.hash.replace('#/', '').split('/').slice(1).join('/');
      window.location.hash = `/${uiState.route}${remainingHash && currentHashRoute === uiState.route ? '/' + remainingHash : ''}`;
    }
  }, [uiState.route]);

  // Global Keyboard Shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      
      if (e.ctrlKey || e.metaKey) {
        if (e.key.toLowerCase() === 'k') { e.preventDefault(); setPaletteOpen(true); }
        if (e.key === '1') { e.preventDefault(); navigate('chat'); }
        if (e.key === '2') { e.preventDefault(); navigate('sessions'); }
        if (e.key === '3') { e.preventDefault(); navigate('workspace'); }
      }
    };
    
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <div className="app-shell">
      <TopBar
        title="Hermes"
        navItems={ROUTES}
        activeRoute={uiState.route}
        onNavigate={navigate}
        onToggleSettings={() => setUiState((current) => toggleModal(current))}
        onToggleInspector={() => setUiState((current) => toggleInspector(current))}
        onToggleDrawer={() => setUiState((current) => toggleDrawer(current))}
        voiceMode={uiState.voiceMode}
        onToggleVoiceMode={() => setUiState((current) => toggleVoiceMode(current))}
        onOpenCommandPalette={() => setPaletteOpen(true)}
      />

      <div className="layout-body">
        <main className="content" aria-label="Main content">
          {!connection.online && (
            <div style={{
              padding: '10px 20px',
              background: 'rgba(239, 68, 68, 0.12)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              borderRadius: '10px',
              margin: '8px 16px 0',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              fontSize: '0.85rem',
              color: '#fca5a5',
            }}>
              <span style={{ fontSize: '1.2rem' }}>⚠️</span>
              <span>Hermes backend is <strong>offline</strong>. Some features (Skills, Memory, Background Jobs) are unavailable.</span>
            </div>
          )}
          <AllRoutes activeRoute={route.id} voiceMode={uiState.voiceMode} />
        </main>

        <Inspector open={uiState.inspectorOpen} activeTab={uiState.inspectorTab} onTabChange={selectInspectorTab} onClose={() => setUiState((current) => toggleInspector(current))} />
      </div>

      <BottomDrawer open={uiState.drawerOpen} activeTab={uiState.drawerTab} onTabChange={selectDrawerTab} />

      <ControlCenterModal 
        open={uiState.modalOpen} 
        activeTab={uiState.modalTab} 
        onTabChange={selectModalTab} 
        onClose={() => setUiState((current) => toggleModal(current))} 
      />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <ArtifactViewer uiState={uiState} setUiState={setUiState} />
      <Toaster />
    </div>
  );
}
