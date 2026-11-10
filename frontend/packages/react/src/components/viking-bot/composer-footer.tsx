import type { ReactNode, RefObject } from 'react';
import type { SenderInstance } from '@ark-design/ui/sender';
import type { VikingBotLabels } from './model.js';
import type { VikingBotReferences } from './capabilities.js';
import { ComposerFileButton } from './composer-attachments.js';
import { ReferenceButton } from './composer-references.js';

export function ComposerFooter({
  references,
  senderRef,
  labels,
  disabled,
  filesDisabled,
  accept,
  onFiles,
  actions,
  submit,
}: {
  senderRef: RefObject<SenderInstance | null>;
  labels: Required<VikingBotLabels>;
  references?: VikingBotReferences;
  disabled?: boolean;
  filesDisabled?: boolean;
  accept?: string;
  onFiles?: (files: readonly File[]) => void;
  actions?: ReactNode;
  submit: ReactNode;
}) {
  return (
    <div className="ov-viking-bot__composer-actions">
      <div className="ov-viking-bot__composer-tools">
        {onFiles ? (
          <ComposerFileButton
            label={labels.attach}
            accept={accept}
            disabled={filesDisabled}
            onFiles={onFiles}
          />
        ) : null}
        {references ? (
          <ReferenceButton
            config={references}
            labels={labels}
            senderRef={senderRef}
            disabled={disabled}
          />
        ) : null}
        {actions}
      </div>
      {submit}
    </div>
  );
}
