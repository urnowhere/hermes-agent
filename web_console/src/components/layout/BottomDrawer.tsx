import type { DrawerTab } from '../../lib/types';
import { cx } from '../../lib/utils';
import { ProcessPanel } from '../drawer/ProcessPanel';
import { TerminalPanel } from '../drawer/TerminalPanel';
import { LogsPage } from '../../pages/LogsPage';
import { BrowserControlPanel } from '../browser/BrowserControlPanel';

const DRAWER_TABS: DrawerTab[] = ['terminal', 'processes', 'logs', 'browser'];

interface BottomDrawerProps {
  open: boolean;
  activeTab: DrawerTab;
  onTabChange(tab: DrawerTab): void;
}

export function BottomDrawer({ open, activeTab, onTabChange }: BottomDrawerProps) {
  return (
    <section className={cx('bottom-drawer', !open && 'bottom-drawer-hidden')} aria-label="Bottom drawer">
      <div className="panel-tabs">
        {DRAWER_TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            className={cx('panel-tab', activeTab === tab && 'panel-tab-active')}
            onClick={() => onTabChange(tab)}
          >
            {tab}
          </button>
        ))}
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {activeTab === 'terminal' ? (
          <TerminalPanel />
        ) : activeTab === 'processes' ? (
          <ProcessPanel />
        ) : activeTab === 'logs' ? (
          <LogsPage />
        ) : activeTab === 'browser' ? (
          <BrowserControlPanel />
        ) : (
          <div style={{ padding: '24px', color: '#94a3b8' }}>
            <h2>{activeTab}</h2>
            <p>Panel content loading...</p>
          </div>
        )}
      </div>
    </section>
  );
}
