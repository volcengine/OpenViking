import { useState } from 'react';
import type { RefObject } from 'react';
import { Modal } from '../../shared/primitives/modal/modal.js';
import { Button } from '../../shared/primitives/button/button.js';
import { PreviewHistorySnapshot } from './preview-history-snapshot.js';
import type { PreviewWorkflowProps } from './preview-workflow-types.js';

/** File content is already saved; snapshot creation has its own request lifecycle. */
export function PreviewSavedSnapshot({
  uri,
  locale,
  text,
  requestOptions,
  triggerRef,
  onUpdated,
  onClose,
}: PreviewWorkflowProps & {
  triggerRef: RefObject<HTMLElement | null>;
}) {
  const [stage, setStage] = useState<'choice' | 'note' | 'created' | 'noop'>(
    'choice',
  );
  const [pending, setPending] = useState(false);
  const completed = stage === 'created' || stage === 'noop';
  const title = stage === 'choice' ? text.snapshotPrompt : text.saveSnapshot;
  return (
    <Modal
      open
      title={title}
      locale={locale}
      triggerRef={triggerRef}
      className="ov-file-browser-workflow ov-file-saved-snapshot"
      onClose={pending ? undefined : onClose}
      maskClosable={!pending}
    >
      <h2>{title}</h2>
      <p className="ov-file-browser-workflow__uri">{uri}</p>
      {completed ? (
        <p role="status">
          {stage === 'created' ? text.snapshotSaved : text.snapshotUnchanged}
        </p>
      ) : (
        <p>
          {stage === 'choice' ? text.snapshotChoice : text.snapshotFileSaved}
        </p>
      )}
      {stage === 'note' ? (
        <PreviewHistorySnapshot
          uri={uri}
          text={text}
          requestOptions={requestOptions}
          onSaved={onUpdated}
          onCompleted={(created) => setStage(created ? 'created' : 'noop')}
          onPendingChange={setPending}
        />
      ) : null}
      <div className="ov-file-browser-workflow__actions">
        <Button disabled={pending} onClick={onClose}>
          {completed
            ? text.done
            : stage === 'choice'
              ? text.skipSnapshot
              : text.cancel}
        </Button>
        {stage === 'choice' ? (
          <Button type="primary" onClick={() => setStage('note')}>
            {text.keepSnapshot}
          </Button>
        ) : null}
      </div>
    </Modal>
  );
}
