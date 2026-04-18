import type { InspectorTab } from '../../lib/types';
import { cx } from '../../lib/utils';
import { MemoryPage } from '../../pages/MemoryPage';
import { LogsPage } from '../../pages/LogsPage';
import { BrowserControlPanel } from '../browser/BrowserControlPanel';
import { RunPanel } from '../inspector/RunPanel';
import { ToolsPanel } from '../inspector/ToolsPanel';
import { TodoPanel } from '../inspector/TodoPanel';
import { SessionPanel } from '../inspector/SessionPanel';
import { HumanPanel } from '../inspector/HumanPanel';

const INSPECTOR_TABS: InspectorTab[] = ['run', 'tools', 'todo', 'session', 'human', 'memory', 'logs', 'browser'];

interface InspectorProps {
  open: boolean;
  activeTab: InspectorTab;
  onTabChange(tab: InspectorTab): void;
  onClose(): void;
}

export function Inspector({ open, activeTab, onTabChange, onClose }: InspectorProps) {
  return (
    <aside className={cx('inspector', !open && 'inspector-hidden')} aria-label="Inspector" style={{ transition: 'width 0.2s ease-out' }}>
      <div className="panel-tabs" style={{ justifyContent: open ? 'flex-start' : 'center', borderBottom: open ? 'none' : 'none' }}>
        <button 
          onClick={onClose} 
          style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', padding: '4px', fontSize: '1rem', outline: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          title={open ? "Collapse Inspector" : "Expand Inspector"}
        >
          {open ? '▶' : '◀'}
        </button>
        {open && INSPECTOR_TABS.map((tab) => (
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
      {open && (
        <div className="panel-body">
          {activeTab === 'run' ? (
            <RunPanel />
          ) : activeTab === 'tools' ? (
            <ToolsPanel />
          ) : activeTab === 'todo' ? (
            <TodoPanel />
          ) : activeTab === 'session' ? (
            <SessionPanel />
          ) : activeTab === 'human' ? (
            <HumanPanel />
          ) : activeTab === 'memory' ? (
            <MemoryPage />
          ) : activeTab === 'logs' ? (
            <LogsPage />
          ) : activeTab === 'browser' ? (
            <BrowserControlPanel />
          ) : (
            <>
              <h2>{activeTab}</h2>
              <p>Inspector scaffold for {activeTab} details.</p>
            </>
          )}
        </div>
      )}
    </aside>
  );
}
