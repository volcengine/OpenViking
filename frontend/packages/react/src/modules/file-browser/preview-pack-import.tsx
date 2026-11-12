import { useId, useState } from 'react';
import { Button } from '../../shared/components/button/button.js';
import { importPreviewPack } from './preview-native.js';
import { PreviewMutationError } from './preview-query-state.js';
import type { PreviewWorkflowProps } from './preview-workflow-types.js';
import { usePreviewMutation } from './use-preview-mutation.js';

export function PreviewPackImport({
  uri,
  canWrite,
  text,
  requestOptions,
  onUpdated,
  onClose,
}: PreviewWorkflowProps & { canWrite: boolean }) {
  const id = useId();
  const [file, setFile] = useState<File>();
  const mutation = usePreviewMutation(uri, requestOptions);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (canWrite && file)
          void mutation.run(
            (client, options) => importPreviewPack(client, uri, file, options),
            onUpdated,
          );
      }}
    >
      <p id={`${id}-hint`}>{text.importHint}</p>
      <label htmlFor={id}>{text.importFile}</label>
      <input
        id={id}
        type="file"
        accept=".ovpack"
        required
        aria-describedby={`${id}-hint`}
        disabled={!canWrite || mutation.pending}
        onChange={(event) => {
          setFile(event.currentTarget.files?.[0]);
          mutation.reset();
        }}
      />
      <PreviewMutationError error={mutation.error} text={text} />
      {mutation.succeeded ? <p role="status">{text.imported}</p> : null}
      <div className="ov-file-browser-workflow__actions">
        <Button size="small" htmlType="button" onClick={onClose}>
          {text.cancel}
        </Button>
        <Button
          size="small"
          type="primary"
          htmlType="submit"
          loading={mutation.pending}
          disabled={!canWrite || !file || mutation.succeeded}
        >
          {text.importSubmit}
        </Button>
      </div>
    </form>
  );
}
