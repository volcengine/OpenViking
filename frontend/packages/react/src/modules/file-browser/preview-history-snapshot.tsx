import { useState } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import { Input } from '../../shared/primitives/input/input.js';
import { savePreviewSnapshot } from './preview-native.js';
import { PreviewMutationError } from './preview-query-state.js';
import { usePreviewMutation } from './use-preview-mutation.js';
import type { PreviewWorkflowProps } from './preview-workflow-types.js';

export function PreviewHistorySnapshot({
  uri,
  text,
  requestOptions,
  onSaved,
}: Pick<PreviewWorkflowProps, 'uri' | 'text' | 'requestOptions'> & {
  onSaved: () => void;
}) {
  const [note, setNote] = useState('');
  const [created, setCreated] = useState(false);
  const mutation = usePreviewMutation(uri, requestOptions);
  const message = note.trim();
  return (
    <form
      className="ov-file-history__snapshot"
      onSubmit={(event) => {
        event.preventDefault();
        if (!message || mutation.pending) return;
        let snapshotCreated = false;
        void mutation.run(
          async (client, options) => {
            const result = await savePreviewSnapshot(
              client,
              uri,
              message,
              options,
            );
            snapshotCreated = result.result === 'created';
          },
          () => {
            setCreated(snapshotCreated);
            if (snapshotCreated) {
              setNote('');
              onSaved();
            }
          },
        );
      }}
    >
      <div className="ov-file-history__snapshot-fields">
        <Input
          size="small"
          aria-label={text.snapshotNote}
          placeholder={text.snapshotNote}
          maxLength={200}
          disabled={mutation.pending}
          value={note}
          onChange={(event) => {
            setNote(event.currentTarget.value);
            mutation.reset();
          }}
        />
        <Button
          size="small"
          type="primary"
          htmlType="submit"
          loading={mutation.pending}
          disabled={!message}
        >
          {text.saveSnapshot}
        </Button>
      </div>
      <PreviewMutationError error={mutation.error} text={text} />
      {mutation.succeeded ? (
        <p role="status">
          {created ? text.snapshotSaved : text.snapshotUnchanged}
        </p>
      ) : null}
    </form>
  );
}
