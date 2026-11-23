import {
  useState,
  type ComponentProps,
  type ReactNode,
  type RefObject,
} from 'react';
import { ContextPicker } from '../../context-picker/index.js';
import { Button } from '../../../shared/primitives/button/button.js';
import type { Locale } from '../../../shared/locale.js';
import type { InteractionLabels } from './labels.js';
import type { VikingBotInteractionDirectoryPicker } from './directory-picker-types.js';
import { resolveDirectoryPermission } from './directory-state.js';
import { DirectoryPermission } from './directory-permission.js';
import { CreateDirectory } from './create-directory.js';

/** HITL 只适配目录答案；弹窗和树由公共 ContextPicker 负责。 */
export function DirectoryPicker({
  picker,
  value,
  labels,
  locale,
  triggerRef,
  onClose,
  onConfirm,
}: {
  picker: VikingBotInteractionDirectoryPicker;
  value: string;
  labels: InteractionLabels;
  locale: Locale;
  triggerRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onConfirm: (uri: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [creating, setCreating] = useState(false);
  const permission = resolveDirectoryPermission(draft, picker.permission);
  const canCreate = Boolean(
    draft &&
    permission.info?.canWrite &&
    picker.operations?.createDirectory &&
    !permission.checking,
  );
  if (creating)
    return (
      <CreateDirectory
        parentUri={draft}
        operations={picker.operations ?? {}}
        labels={labels}
        locale={locale}
        triggerRef={triggerRef}
        onClose={() => setCreating(false)}
        onCreated={onConfirm}
      />
    );
  return (
    <ContextPicker
      {...picker}
      open
      locale={locale}
      triggerRef={triggerRef}
      rows={picker.rows.filter(
        (row) => row.kind !== 'entry' || row.type === 'directory',
      )}
      value={
        draft
          ? [
              {
                uri: draft,
                name: draft.split('/').pop() ?? draft,
                type: 'directory',
              },
            ]
          : []
      }
      title={labels.directoryPickerTitle}
      labels={{
        search: labels.directorySearch,
        selected: labels.directorySelected,
        confirm: labels.directoryPickerConfirm,
        cancel: labels.cancel,
        empty: labels.directoryEmpty,
      }}
      onChange={(entries) => {
        const uri = entries[0]?.uri ?? '';
        setDraft(uri);
        picker.onSelectionChange?.(uri);
      }}
      onConfirm={() => onConfirm(draft)}
      onCancel={() => {
        picker.onSelectionChange?.(value);
        onClose();
      }}
      slots={{
        content: picker.permission ? (
          <DirectoryPermission permission={permission} labels={labels} />
        ) : undefined,
        footerStart: picker.operations?.createDirectory ? (
          <Button
            type="outline"
            disabled={!canCreate}
            title={!canCreate ? labels.directoryPermissionHint : undefined}
            onClick={() => setCreating(true)}
          >
            {labels.directoryCreate}
          </Button>
        ) : null,
      }}
    />
  );
}

/** The caller keys this boundary by availability; disabling discards only popup state. */
export function DirectoryPickerTrigger({
  children,
  disabled,
  ...props
}: Omit<ComponentProps<typeof DirectoryPicker>, 'onClose'> & {
  disabled: boolean;
  children: (open: boolean, show: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {children(open, () => {
        if (disabled) return;
        props.picker.onQueryChange?.('');
        setOpen(true);
      })}
      {open && !disabled ? (
        <DirectoryPicker
          {...props}
          onClose={() => setOpen(false)}
          onConfirm={(uri) => {
            setOpen(false);
            props.onConfirm(uri);
          }}
        />
      ) : null}
    </>
  );
}
