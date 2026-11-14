import { Button } from '../../shared/primitives/button/button.js';
import { useRef } from 'react';
import { VikingBotIcon } from '../../icons/viking-bot-icons.js';
import type { VikingBotAttachment, VikingBotLabels } from './model.js';
import { VikingBotAttach } from './composer-controls.js';

export function attachmentsAreReady(items: readonly VikingBotAttachment[]) {
  return items.every(({ status = 'ready' }) => status === 'ready');
}

export function ComposerFileButton({
  accept,
  disabled,
  label,
  onFiles,
}: {
  accept?: string;
  disabled?: boolean;
  label: string;
  onFiles: (files: readonly File[]) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple
        hidden
        onChange={(event) => {
          onFiles([...(event.target.files ?? [])]);
          event.target.value = '';
        }}
      />
      <VikingBotAttach
        label={label}
        disabled={disabled}
        onClick={() => input.current?.click()}
      />
    </>
  );
}

export function ComposerAttachments({
  items,
  onRemove,
  disabled,
  labels,
}: {
  items: readonly VikingBotAttachment[];
  onRemove?: (item: VikingBotAttachment) => void;
  disabled?: boolean;
  labels: Required<VikingBotLabels>;
}) {
  if (!items.length) return null;
  return (
    <ul className="ov-viking-bot__attachments">
      {items.map((item) => (
        <li key={item.key}>
          <span>{item.name}</span>
          {item.status === 'error' ? (
            <span className="ov-viking-bot__attachment-status" role="alert">
              {item.errorMessage ?? labels.attachmentUploadFailed}
            </span>
          ) : item.status === 'pending' || item.status === 'uploading' ? (
            <span role="status">{labels.uploadingAttachment}</span>
          ) : null}
          {onRemove ? (
            <Button
              size="mini"
              type="text"
              iconOnly
              htmlType="button"
              aria-label={`${labels.removeAttachment}: ${item.name}`}
              disabled={disabled}
              onClick={() => onRemove(item)}
            >
              <VikingBotIcon name="remove" width={12} height={12} />
            </Button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
