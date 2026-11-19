import {
  WikiDashboard,
  type WikiDashboardProps,
} from '../../components/wiki-dashboard/index.js';
import { useContext } from 'react';
import { FilePreviewEditContext } from '../../components/file-preview/file-preview-edit-context.js';
import type { OvRequestOptions } from '../../client/types.js';
import { useWikiDashboard } from './use-wiki-dashboard.js';

type Props = Pick<
  WikiDashboardProps,
  'locale' | 'labels' | 'className' | 'style'
> & {
  uri: string;
  kind: 'directory' | 'file';
  onOpenUri?: (uri: string) => void;
  onOpenDashboard?: (uri: string) => void;
  requestOptions?: OvRequestOptions;
  refreshGeneration?: number;
};
export function WikiDashboardPane({
  uri,
  kind,
  requestOptions,
  refreshGeneration,
  onOpenUri,
  onOpenDashboard,
  ...props
}: Props) {
  const editConflict = useContext(FilePreviewEditContext)?.editFile;
  const query = useWikiDashboard({ uri, requestOptions, refreshGeneration });
  const common = {
    ...props,
    loading: query.loading,
    error: query.error,
    onRetry: query.retry,
  };
  if ((query.data?.kind ?? kind) === 'file')
    return (
      <WikiDashboard
        {...common}
        key={uri}
        kind="file"
        data={query.data?.kind === 'file' ? query.data.data : undefined}
        onOpenUri={onOpenUri && (() => onOpenUri(uri))}
        onEditConflict={
          editConflict && (({ matchText }) => editConflict(matchText))
        }
      />
    );
  return (
    <WikiDashboard
      {...common}
      key={uri}
      kind="directory"
      data={query.data?.kind === 'directory' ? query.data.data : undefined}
      onOpenUri={onOpenUri}
      onOpenDashboard={onOpenDashboard}
    />
  );
}
