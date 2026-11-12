import { useId, useState } from 'react';
import type { RefObject } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import type { Locale } from '../../shared/locale.js';
import { resolveFilePreviewLabels } from './file-preview-labels.js';

export function FilePreviewVersionNote({
  locale,
  triggerRef,
  saving,
  error,
  onClose,
  onConfirm,
}: {
  locale: Locale;
  triggerRef: RefObject<HTMLElement | null>;
  saving: boolean;
  error?: string;
  onClose: () => void;
  onConfirm: (message: string) => Promise<void>;
}) {
  const id = useId();
  const [note, setNote] = useState('');
  const text = resolveFilePreviewLabels(locale);
  return (
    <Modal
      open
      locale={locale}
      title={text.versionNoteTitle}
      triggerRef={triggerRef}
      onClose={saving ? undefined : onClose}
      className="ov-file-preview-version-note"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!saving) void onConfirm(note.trim());
        }}
      >
        <h2>{text.versionNoteTitle}</h2>
        <label htmlFor={id}>{text.versionNoteLabel}</label>
        <textarea
          id={id}
          data-autofocus
          maxLength={1000}
          placeholder={text.versionNotePlaceholder}
          value={note}
          readOnly={saving}
          onChange={(event) => setNote(event.target.value)}
        />
        {error ? <p role="alert">{error}</p> : null}
        <div className="ov-file-preview-version-note__actions">
          <Button onClick={onClose} disabled={saving}>
            {text.versionNoteCancel}
          </Button>
          <Button type="primary" htmlType="submit" loading={saving}>
            {text.versionNoteConfirm}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
