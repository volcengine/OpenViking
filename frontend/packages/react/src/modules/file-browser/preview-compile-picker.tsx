import { useOvClient } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { ContextPicker } from '../../components/context-picker/context-picker.js';
import type { ContextPickerProps } from '../../components/context-picker/types.js';
import { useFsTree } from './use-fs-tree.js';

export function PreviewCompilePicker({
  field,
  requestOptions,
  ...props
}: Omit<ContextPickerProps, 'rows'> & {
  field: 'from' | 'skill';
  requestOptions?: OvRequestOptions;
}) {
  const tree = useFsTree({
    client: useOvClient(),
    rootUri: 'viking://',
    defaultExpandedUri: props.value[0]?.uri,
    requestOptions,
  });
  return (
    <ContextPicker
      {...props}
      className={['ov-compile-picker', props.className]
        .filter(Boolean)
        .join(' ')}
      rows={tree.rows.map((row) =>
        row.kind !== 'entry'
          ? row
          : {
              ...row,
              selectable:
                row.selectable !== false &&
                !row.disabled &&
                (row.type === 'directory' ||
                  (field === 'skill' && row.uri.endsWith('/SKILL.md'))),
            },
      )}
      loading={tree.isInitializing}
      isReady={!tree.isInitializing && tree.isReady}
      operations={{
        toggleExpand: tree.toggleExpand,
        loadMore: (row) => tree.loadMore(row.parentId),
        retry: (row) => tree.retry(row.parentId ?? 'viking://'),
      }}
    />
  );
}
