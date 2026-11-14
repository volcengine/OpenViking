import { useId, useState, type RefObject } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import type { Locale } from '../../shared/locale.js';
import type { FilePreviewLabels } from './types.js';

export function FilePreviewVersionNote({
  locale,
  text,
  triggerRef,
  onClose,
  onConfirm,
}: {
  locale?: Locale;
  text: Required<FilePreviewLabels>;
  triggerRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onConfirm: (note: string) => void;
}) {
  const id = useId();
  const [note, setNote] = useState('');
  return (
    <Modal
      open
      title={text.versionNoteTitle}
      locale={locale}
      triggerRef={triggerRef}
      onClose={onClose}
      className="ov-file-preview-version-note"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onConfirm(note.trim());
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
          onChange={(event) => setNote(event.target.value)}
        />
        <div className="ov-file-preview-version-note__actions">
          <Button onClick={onClose}>{text.versionNoteCancel}</Button>
          <Button type="primary" htmlType="submit">
            {text.versionNoteConfirm}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
