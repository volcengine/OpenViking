import { useEffect, useId, useRef } from 'react';
import type { AuthAgreements, AuthLabels } from './types.js';

export function AgreementLinks({ agreements }: { agreements: AuthAgreements }) {
  return (
    <>
      {agreements.items.map((item, index) => (
        <span key={`${item.href}-${index}`}>
          {' '}
          <a href={item.href} target="_blank" rel="noopener noreferrer">
            {item.label}
          </a>
        </span>
      ))}
    </>
  );
}
export function AgreementDialog({
  agreements,
  labels,
  onCancel,
  onAgree,
}: {
  agreements: AuthAgreements;
  labels: AuthLabels;
  onCancel: () => void;
  onAgree: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  // Synchronize the native modal with this mounted confirmation. showModal is only
  // available after DOM commit; native modality traps focus and restores it on close.
  // Close on unmount, including Strict Mode cleanup, so no stale modal remains active.
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  const text = agreements.confirmation;
  return (
    <dialog
      ref={dialog}
      className="ov-auth ov-auth__dialog"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <h2 id={titleId}>{text?.title ?? labels.agreementTitle}</h2>
      <p id={descriptionId}>
        {text?.description ?? labels.agreementDescription}
        <AgreementLinks agreements={agreements} />
      </p>
      <div className="ov-auth__actions">
        <button type="button" onClick={onCancel}>
          {text?.cancelText ?? labels.cancel}
        </button>
        <button className="ov-auth__primary" type="button" onClick={onAgree}>
          {text?.confirmText ?? labels.agree}
        </button>
      </div>
    </dialog>
  );
}
