import type { FilePreviewAction } from '../../components/file-preview/index.js';
import type { FileBrowserPreviewActions } from './preview-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import type { previewPolicy } from './preview-policy.js';
import HistoryIcon from '../../../../icons/svg/file-preview/history.svg?react';
import ImportIcon from '../../../../icons/svg/file-preview/import.svg?react';
import ExportIcon from '../../../../icons/svg/file-preview/export.svg?react';
import DeleteIcon from '../../../../icons/svg/file-preview/delete.svg?react';
import EditIcon from '../../../../icons/svg/file-preview/edit.svg?react';

export function defaultPreviewActions({
  policy,
  options,
  text,
  canDelete,
  show,
  deleteFile,
  download,
}: {
  policy: ReturnType<typeof previewPolicy>;
  options?: FileBrowserPreviewActions;
  text: ResolvedLabels;
  canDelete: boolean;
  show: (kind: 'history' | 'importPack' | 'configure') => void;
  deleteFile: () => void;
  download: (pack: boolean) => Promise<unknown>;
}) {
  const contentActions: FilePreviewAction[] = [];
  const packActions: FilePreviewAction[] = [];
  const destructiveActions: FilePreviewAction[] = [];
  if (policy.download && options?.download !== false)
    contentActions.push({
      key: 'download',
      label: text.download,
      icon: <ImportIcon width={16} height={16} />,
      onClick: () => download(false),
    });
  if (policy.history && options?.history !== false)
    contentActions.push({
      key: 'history',
      pinned: policy.fileHistory,
      label: text.history,
      icon: <HistoryIcon width={16} height={16} />,
      onClick: () => show('history'),
    });
  if (policy.configure && options?.configure !== false)
    contentActions.push({
      key: 'configure',
      label: text.workflow.configure,
      icon: <EditIcon width={16} height={16} />,
      onClick: () => show('configure'),
    });
  if (policy.importPack && options?.importPack !== false)
    packActions.push({
      key: 'importPack',
      label: text.importPack,
      icon: <ImportIcon width={16} height={16} />,
      onClick: () => show('importPack'),
    });
  if (policy.exportPack && options?.exportPack !== false)
    packActions.push({
      key: 'exportPack',
      label: text.exportPack,
      icon: <ExportIcon width={16} height={16} />,
      onClick: () => download(true),
    });
  if (canDelete && options?.delete !== false)
    destructiveActions.push({
      key: 'delete',
      label: text.deleteFile,
      icon: <DeleteIcon width={16} height={16} />,
      className: 'ov-file-browser__preview-action--danger',
      onClick: deleteFile,
    });
  return [contentActions, packActions, destructiveActions];
}
