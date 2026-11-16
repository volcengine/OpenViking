import { useId, useState } from 'react';
import { DocumentIcon } from '../../icons/index.js';
import UploadIcon from '../../../../icons/svg/file-preview/import.svg?react';
import { Button } from '../../shared/primitives/button/button.js';
import { importPreviewPack } from './preview-native.js';
import { PreviewMutationError } from './preview-query-state.js';
import type { PreviewWorkflowProps } from './preview-workflow-types.js';
import { usePreviewMutation } from './use-preview-mutation.js';

export function PreviewPackImport({
  uri,
  canWrite,
  locale,
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
            'file_browser_pack_import',
          );
      }}
    >
      <p id={`${id}-hint`}>{text.importHint}</p>
      <div className="ov-pack-import__upload">
        <input
          id={id}
          name="file"
          type="file"
          accept=".ovpack"
          required
          aria-label={text.importFile}
          aria-describedby={`${id}-hint`}
          disabled={!canWrite || mutation.pending}
          onChange={(event) => {
            setFile(event.currentTarget.files?.[0]);
            mutation.reset();
          }}
        />
        <label htmlFor={id}>
          {file ? (
            <DocumentIcon width={28} height={28} />
          ) : (
            <UploadIcon width={28} height={28} />
          )}
          <strong>{file?.name ?? text.importDrop}</strong>
          <span>
            {file
              ? `${new Intl.NumberFormat().format(file.size)} B`
              : text.importFormat}
          </span>
          <span className="ov-pack-import__choose">{text.importChoose}</span>
        </label>
      </div>
      <PreviewMutationError
        error={mutation.error}
        title={text.failureTitle(text.importSubmit)}
        locale={locale}
      />
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
