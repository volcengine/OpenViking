import { useRef, useState } from 'react';
import { Button } from '../../shared/components/button/button.js';
import { Tooltip } from '../../shared/components/tooltip/tooltip.js';
import { FilePreviewMenu } from './file-preview-menu.js';
import { normalizeFilePreviewActions } from './normalize-file-preview-actions.js';
import type {
  FilePreviewAction,
  FilePreviewActionItems,
} from './action-types.js';
import type { FilePreviewLabels } from './types.js';

export function FilePreviewActionList({
  items,
  disabled,
  text,
}: {
  items: FilePreviewActionItems;
  disabled: boolean;
  text: Required<FilePreviewLabels>;
}) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<{ reason: unknown }>();
  const running = useRef(false);
  const groups = normalizeFilePreviewActions(items);
  const count = groups.reduce((total, group) => total + group.length, 0);
  const execute = async (action: FilePreviewAction) => {
    if (running.current || disabled || action.disabled) return;
    running.current = true;
    setPending(true);
    setFailure(undefined);
    try {
      await action.onClick();
    } catch (reason) {
      setFailure({ reason });
    } finally {
      running.current = false;
      setPending(false);
    }
  };
  const action = groups[0]?.[0];
  if (!action) return null;
  return (
    <>
      {count === 1 ? (
        <Tooltip content={action.label}>
          <Button
            size="small"
            type="default"
            iconOnly
            className={['ov-file-preview__toolbar-button', action.className]
              .filter(Boolean)
              .join(' ')}
            aria-label={action.label}
            disabled={disabled || action.disabled}
            loading={pending}
            onClick={() => void execute(action)}
          >
            <span className="ov-file-preview__action-icon" aria-hidden="true">
              {action.icon}
            </span>
          </Button>
        </Tooltip>
      ) : (
        <FilePreviewMenu
          groups={groups}
          disabled={disabled}
          pending={pending}
          text={text}
          onSelect={(selected) => void execute(selected)}
        />
      )}
      {failure ? (
        <span className="ov-file-preview__action-error" role="alert">
          {text.actionFailed}
          {failure.reason instanceof Error
            ? `: ${failure.reason.message}`
            : typeof failure.reason === 'string'
              ? `: ${failure.reason}`
              : ''}
        </span>
      ) : null}
    </>
  );
}
