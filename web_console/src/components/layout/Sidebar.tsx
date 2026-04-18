import type { NavItem, PrimaryRoute } from '../../lib/types';
import { cx } from '../../lib/utils';

interface SidebarProps {
  items: readonly NavItem[];
  activeRoute: PrimaryRoute;
  onNavigate(route: PrimaryRoute): void;
}

export function Sidebar({ items, activeRoute, onNavigate }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Primary navigation">
      <nav>
        <ul>
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                aria-label={item.label}
                className={cx('nav-button', activeRoute === item.id && 'nav-button-active')}
                onClick={() => onNavigate(item.id)}
              >
                <strong>{item.label}</strong>
                <span>{item.description}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
