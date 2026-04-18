import { SettingsPage } from '../../pages/SettingsPage';
import { ToolsPage } from '../../pages/ToolsPage';
import { GatewayPage } from '../../pages/GatewayPage';
import { SkillsPage } from '../../pages/SkillsPage';
import { AutomationsPage } from '../../pages/AutomationsPage';
import { InsightsPage } from '../../pages/InsightsPage';
import { ErrorBoundary } from '../shared/ErrorBoundary';
import type { ModalTab } from '../../lib/types';
import { cx } from '../../lib/utils';
import './ControlCenterModal.css';

interface ControlCenterModalProps {
  open: boolean;
  activeTab: ModalTab;
  onTabChange(tab: ModalTab): void;
  onClose(): void;
}

const MODAL_TABS: { id: ModalTab; label: string }[] = [
  { id: 'settings', label: 'General Settings' },
  { id: 'tools', label: 'Tools & Toolsets' },
  { id: 'gateway', label: 'Messaging Gateway' },
  { id: 'skills', label: 'Agent Skills' },
  { id: 'automations', label: 'Automations (Cron)' },
  { id: 'insights', label: 'Analytics & Insights' }
];

export function ControlCenterModal({ open, activeTab, onTabChange, onClose }: ControlCenterModalProps) {
  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-container" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <h2>Control Center</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>
        
        <div className="modal-layout">
          <aside className="modal-sidebar">
            <nav className="modal-nav">
              {MODAL_TABS.map((tab) => (
                <button
                  key={tab.id}
                  className={cx('modal-nav-button', activeTab === tab.id && 'modal-nav-active')}
                  onClick={() => onTabChange(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          </aside>
          
          <main className="modal-content">
            <ErrorBoundary>
              {activeTab === 'settings' && <SettingsPage />}
              {activeTab === 'tools' && <ToolsPage />}
              {activeTab === 'gateway' && <GatewayPage />}
              {activeTab === 'skills' && <SkillsPage />}
              {activeTab === 'automations' && <AutomationsPage />}
              {activeTab === 'insights' && <InsightsPage />}
            </ErrorBoundary>
          </main>
        </div>
      </div>
    </div>
  );
}
