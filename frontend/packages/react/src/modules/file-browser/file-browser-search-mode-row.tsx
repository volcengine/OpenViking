import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

export function FileBrowserSearchModeRow({
  mode,
  count,
  text,
  onSelectMode,
}: {
  mode: SearchMode;
  count: number | null;
  text: ResolvedLabels;
  onSelectMode: (mode: SearchMode) => void;
}) {
  return (
    <div className="ov-file-browser-search__summary">
      {count !== null ? (
        <>
          <span>{text.searchResultCount(count)}</span>
          <span aria-hidden="true">・</span>
        </>
      ) : null}
      <span className="ov-file-browser-search__mode-chip">{mode}</span>
      <span>{text.searchVia}</span>
      <FileBrowserSearchModeMenu
        value={mode}
        text={text}
        onSelect={onSelectMode}
      />
    </div>
  );
}
