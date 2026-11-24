import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import type { FilePreviewLabels } from './label-types.js';

export function useFilePreviewLeave(
  identity: string,
  dirty: boolean,
  busy: boolean,
  text: Required<FilePreviewLabels>,
) {
  const [pendingKey, setPendingKey] = useState<string>();
  const pending = useRef<{ key: string; resolve: (leave: boolean) => void }>(
    undefined,
  );
  const triggerRef = useRef<HTMLDivElement>(null);
  const descriptionId = useId();
  // An external selection change or unmount invalidates the pending navigation.
  // Neither is guaranteed to go through this preview's event handlers.
  useEffect(
    () => () => {
      if (pending.current?.key !== identity) return;
      pending.current.resolve(false);
      pending.current = undefined;
    },
    [identity],
  );
  const finish = (leave: boolean) => {
    if (pending.current?.key !== identity) return;
    pending.current.resolve(leave);
    pending.current = undefined;
    setPendingKey(undefined);
  };
  const confirm = (): boolean | Promise<boolean> => {
    if (busy || pending.current) return false;
    if (!dirty) return true;
    return new Promise((resolve) => {
      pending.current = { key: identity, resolve };
      setPendingKey(identity);
    });
  };
  return {
    confirm,
    triggerRef,
    dialog: (
      <Modal
        open={pendingKey === identity && pending.current?.key === identity}
        title={text.discardTitle}
        triggerRef={triggerRef}
        maskClosable={false}
        onClose={() => finish(false)}
        className="ov-file-preview-version-note"
      >
        <h2>{text.discardTitle}</h2>
        <p id={descriptionId}>{text.discardDescription}</p>
        <div className="ov-file-preview-version-note__actions">
          <Button
            data-autofocus
            aria-describedby={descriptionId}
            onClick={() => finish(false)}
          >
            {text.keepEditing}
          </Button>
          <Button type="primary" onClick={() => finish(true)}>
            {text.discardDraft}
          </Button>
        </div>
      </Modal>
    ),
  };
}

/** Keep clean navigation synchronous, and resume deferred navigation only on confirmation. */
export function afterLeaving(
  decision: boolean | Promise<boolean>,
  action: () => void,
) {
  if (decision === true) {
    action();
    return true;
  }
  if (decision)
    void decision.then((confirmed) => {
      if (confirmed) action();
    });
  return false;
}
