import { FileTreeToolbar } from '../../components/file-tree/file-tree-toolbar.js';

/** Host-owned search intent and module-owned refresh share the file-tree controls. */
export interface FileBrowserToolbarProps {
  refreshing: boolean;
  canRefresh: boolean;
  placeholder: string;
  refreshLabel: string;
  onSearch?: () => void;
  onRefresh: () => void;
}

export function FileBrowserToolbar({
  refreshing,
  canRefresh,
  placeholder,
  refreshLabel,
  onSearch,
  onRefresh,
}: FileBrowserToolbarProps) {
  return (
    <FileTreeToolbar
      className="ov-file-browser__toolbar"
      search
      refresh={{ disabled: !canRefresh }}
      refreshing={refreshing}
      text={{ search: placeholder, refresh: refreshLabel }}
      onSearch={onSearch}
      onRefresh={onRefresh}
    />
  );
}
