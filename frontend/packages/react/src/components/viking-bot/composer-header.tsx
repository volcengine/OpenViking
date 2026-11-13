import { ComposerAttachments } from './composer-attachments.js';
import { ReferenceTags } from './composer-reference-tags.js';
import type { VikingBotComposerProps } from './types.js';
import type { VikingBotLabels } from './model.js';

export function ComposerHeader({
  attachments = [],
  references,
  onAttachmentRemove,
  onOpenUri,
  labels,
  disabled,
}: Pick<
  VikingBotComposerProps,
  'attachments' | 'references' | 'onAttachmentRemove' | 'onOpenUri' | 'disabled'
> & { labels: Required<VikingBotLabels> }) {
  return (
    <>
      <ReferenceTags
        config={references}
        labels={labels}
        onOpenUri={onOpenUri}
        disabled={disabled}
      />
      <ComposerAttachments
        items={attachments}
        onRemove={onAttachmentRemove}
        labels={labels}
        disabled={disabled}
      />
    </>
  );
}
