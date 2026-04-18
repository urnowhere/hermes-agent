import type { DrawerTab, InspectorTab, PrimaryRoute, UiState } from '../lib/types';

export const initialUiState: UiState = {
  route: 'chat',
  inspectorOpen: true,
  inspectorTab: 'run',
  drawerOpen: false,
  drawerTab: 'terminal',
  modalOpen: false,
  modalTab: 'settings',
  voiceMode: false,
  artifactOpen: false,
  artifactContent: null,
  artifactType: null
};

export function setRoute(state: UiState, route: PrimaryRoute): UiState {
  return { ...state, route };
}

export function toggleInspector(state: UiState): UiState {
  return { ...state, inspectorOpen: !state.inspectorOpen };
}

export function setInspectorTab(state: UiState, inspectorTab: InspectorTab): UiState {
  return { ...state, inspectorTab, inspectorOpen: true };
}

export function toggleDrawer(state: UiState, drawerTab?: DrawerTab): UiState {
  return {
    ...state,
    drawerOpen: drawerTab ? true : !state.drawerOpen,
    drawerTab: drawerTab ?? state.drawerTab
  };
}

export function toggleModal(state: UiState, modalTab?: UiState['modalTab']): UiState {
  return {
    ...state,
    modalOpen: modalTab ? true : !state.modalOpen,
    modalTab: modalTab ?? state.modalTab
  };
}

export function toggleVoiceMode(state: UiState): UiState {
  return { ...state, voiceMode: !state.voiceMode };
}

export function openArtifact(state: UiState, artifactContent: string, artifactType: string): UiState {
  return { ...state, artifactOpen: true, artifactContent, artifactType };
}

export function closeArtifact(state: UiState): UiState {
  return { ...state, artifactOpen: false, artifactContent: null, artifactType: null };
}
