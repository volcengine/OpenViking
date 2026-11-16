import { usePreviewActionExposure } from '../../shared/analytics/preview-actions.js';
import { Button } from '../../shared/primitives/button/button.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
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
  pending,
  execute,
}: {
  items: FilePreviewActionItems;
  disabled: boolean;
  text: Required<FilePreviewLabels>;
  pending: boolean;
  execute: (action: FilePreviewAction) => Promise<void>;
}) {
  const exposure = usePreviewActionExposure(items);
  const groups = normalizeFilePreviewActions(items);
  const count = groups.reduce((total, group) => total + group.length, 0);
  const action = groups[0]?.[0];
  if (!action) return null;
  return (
    <>
      {count === 1 ? (
        <Tooltip content={action.label}>
          <Button
            ref={exposure}
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
            icon={
              <span className="ov-file-preview__action-icon" aria-hidden="true">
                {action.icon}
              </span>
            }
          />
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
    </>
  );
}
