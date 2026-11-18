import { hasOvpackHeader, hasOvpackName } from '../../shared/ovpack-file.js';
import { useCallback, useId, useMemo, useRef, useState } from 'react';
import { DocumentIcon } from '../../icons/index.js';
import UploadIcon from '../../../../icons/svg/file-preview/import.svg?react';
import { Button } from '../../shared/primitives/button/button.js';
import { Toast } from '../../shared/primitives/toast/toast.js';
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
  onSelectPackFile,
  packConfirmationPending,
}: PreviewWorkflowProps & {
  canWrite: boolean;
  onSelectPackFile?: (file: File) => void;
  packConfirmationPending?: boolean;
}) {
  const id = useId();
  const [file, setFile] = useState<File>();
  const nextError = useRef(0);
  const [formatError, setFormatError] = useState<string>();
  const dismissFormatError = useCallback((noticeId: string) => {
    setFormatError((current) => (current === noticeId ? undefined : current));
  }, []);
  const notices = useMemo(
    () =>
      formatError
        ? [
            {
              id: formatError,
              type: 'error' as const,
              content: text.importInvalidFormat,
            },
          ]
        : [],
    [formatError, text.importInvalidFormat],
  );
  const mutation = usePreviewMutation(uri, requestOptions);
  const pending = packConfirmationPending || mutation.pending;
  async function changeFile(input: HTMLInputElement) {
    if (!canWrite || pending) return;
    const picked = input.files?.[0];
    setFile(undefined);
    setFormatError(undefined);
    mutation.reset();
    if (!picked) return;
    const valid =
      hasOvpackName(picked.name) &&
      (await hasOvpackHeader(picked).catch(() => false));
    if (!input.isConnected || input.files?.[0] !== picked) return;
    if (valid) setFile(picked);
    else {
      input.value = '';
      setFormatError(`pack-import-format-${++nextError.current}`);
    }
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!canWrite || !file || pending) return;
        if (onSelectPackFile) onSelectPackFile(file);
        else
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
          disabled={!canWrite || pending}
          onChange={(event) => void changeFile(event.currentTarget)}
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
      <Toast locale={locale} notices={notices} onRemove={dismissFormatError} />
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
          loading={pending}
          disabled={!canWrite || !file || pending || mutation.succeeded}
        >
          {text.importSubmit}
        </Button>
      </div>
    </form>
  );
}
