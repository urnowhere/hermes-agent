interface FileTreeProps {
  files: string[];
  selectedPath: string;
  onSelect(path: string): void;
}

export function FileTree({ files, selectedPath, onSelect }: FileTreeProps) {
  return (
    <section className="workspace-card" aria-label="File tree" style={{ display: 'flex', flexDirection: 'column', maxHeight: '50vh' }}>
      <div className="workspace-card-header" style={{ flexShrink: 0 }}>
        <h2>File tree</h2>
        <p>Browse workspace files and directories.</p>
      </div>
      <ul className="workspace-list" style={{ overflowY: 'auto', flex: 1, paddingRight: '4px' }}>
        {files.map((path) => (
          <li key={path}>
            <button
              type="button"
              className={selectedPath === path ? 'workspace-row workspace-row-active' : 'workspace-row'}
              onClick={() => onSelect(path)}
              title={path}
              style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                display: 'block',
              }}
            >
              {path.split('/').pop() || path}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
