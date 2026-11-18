import { useExposure } from '../../shared/analytics/use-exposure.js';
import {
  ErrorNotification,
  errorMessage,
} from '../../shared/feedback/error-notification.js';
import type { RefObject } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import WarningIcon from '../../../../icons/svg/file-preview/warning.svg?react';
import type { Locale } from '../../shared/locale.js';
import type { ResolvedLabels } from './file-browser-labels.js';

export function FileBrowserDeleteDialog({
  open,
  uri,
  busy,
  error,
  text,
  locale,
  triggerRef,
  onClose,
  onConfirm,
}: {
  open: boolean;
  uri: string;
  busy: boolean;
  error?: unknown;
  text: ResolvedLabels;
  locale: Locale;
  triggerRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const exposure = useExposure(
    'file_browser_delete_confirm_dialog_show',
    open,
    undefined,
    open,
  );
  return (
    <Modal
      open={open}
      title={text.deleteTitle}
      triggerRef={triggerRef}
      locale={locale}
      className="ov-file-browser-delete"
      onClose={onClose}
    >
      <h2 ref={exposure} className="ov-file-browser-delete__title">
        <span className="ov-file-browser-delete__warning" aria-hidden="true">
          <WarningIcon width={12} height={12} />
        </span>
        {text.deleteTitle}
      </h2>
      <p className="ov-file-browser-delete__message">
        {text.deleteMessage(uri.split('/').pop() ?? uri)}
      </p>
      {error !== undefined ? (
        <ErrorNotification
          title={text.deleteFailed}
          identity={error}
          error={error}
          locale={locale}
        >
          {errorMessage(error)}
        </ErrorNotification>
      ) : null}
      <div className="ov-file-browser-delete__actions">
        <Button size="small" type="default" onClick={onClose}>
          {text.deleteCancel}
        </Button>
        <Button
          size="small"
          type="primary"
          className="ov-file-browser-delete__confirm"
          loading={busy}
          onClick={onConfirm}
        >
          {text.deleteConfirm}
        </Button>
      </div>
    </Modal>
  );
}
