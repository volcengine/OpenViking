import { useEffect, useState, type RefObject } from 'react';
import { useOvClient } from '../../../client/provider.js';
import { Button } from '../../../shared/primitives/button/button.js';
import type { OvClient, OvRequestOptions } from '../../../client/types.js';
import {
  ContextPicker,
  type ContextPickerEntry,
} from '../../../components/context-picker/index.js';
import { useLocale } from '../../../shared/locale-context.js';
import { useConversationReferences } from '../use-conversation-references.js';
import { hitlLabels } from './locales.js';
import { SkillUpload } from './skill-upload.js';

export interface PickerScope {
  client?: OvClient;
  requestOptions?: OvRequestOptions;
  scopeKey?: string;
}

export function CompilePicker({
  mode,
  initial,
  scope,
  triggerRef,
  onConfirm,
  onCancel,
  onOpenUri,
}: {
  mode: 'source' | 'skill';
  initial: readonly ContextPickerEntry[];
  scope: PickerScope;
  triggerRef: RefObject<HTMLElement | null>;
  onConfirm: (items: readonly ContextPickerEntry[]) => void;
  onCancel: () => void;
  onOpenUri?: (uri: string) => void;
}) {
  const locale = useLocale();
  const labels = hitlLabels(locale);
  const uploadClient = useOvClient();
  const [uploading, setUploading] = useState(false);
  const [value, setValue] = useState(initial);
  const [query, setQuery] = useState('');
  const { references, expandDirectory } = useConversationReferences({
    ...scope,
    directoryClient: scope.client,
    enabled: true,
  });
  const open = references?.onOpenChange;
  // Mounting this modal opens a directory browser and subscribes to its data.
  // Cleanup closes it even when the host dismisses the whole clarification.
  useEffect(() => {
    open?.(true);
    return () => open?.(false);
  }, [open]);
  const source = mode === 'source';
  const rows = (references?.rows ?? [])
    .filter(
      (row) =>
        row.kind !== 'entry' ||
        row.type === 'directory' ||
        (!source && row.name === 'SKILL.md'),
    )
    .map((row) =>
      !source && row.kind === 'entry' && row.type === 'directory'
        ? { ...row, selectable: false }
        : row,
    );
  if (uploading && uploadClient)
    return (
      <SkillUpload
        client={uploadClient}
        options={scope.requestOptions}
        triggerRef={triggerRef}
        onCancel={() => setUploading(false)}
        onUploaded={(entry) => onConfirm([entry])}
      />
    );
  return (
    <ContextPicker
      open
      locale={locale}
      rootOrder="context"
      renderRowTooltip={() => null}
      title={
        source ? labels.titles.compile_source : labels.titles.compile_skill
      }
      triggerRef={triggerRef}
      rows={rows}
      value={value}
      multiple={source}
      loading={references?.isLoading}
      error={references?.error}
      query={query}
      onQueryChange={(text) => {
        setQuery(text);
        references?.onQueryChange?.(text);
      }}
      operations={{
        toggleExpand: async (row) => {
          await expandDirectory?.(row.uri);
        },
        loadMore: async (row) => {
          await references?.onLoadMore?.(row.parentId);
        },
        retry: async (row) => {
          await references?.onRetry?.(row.parentId);
        },
      }}
      onChange={setValue}
      onCancel={onCancel}
      onConfirm={onConfirm}
      onOpenEntry={onOpenUri && ((entry) => onOpenUri(entry.uri))}
      slots={
        !source && uploadClient
          ? {
              footerStart: (
                <Button type="outline" onClick={() => setUploading(true)}>
                  {labels.uploadSkill}
                </Button>
              ),
            }
          : undefined
      }
    />
  );
}
