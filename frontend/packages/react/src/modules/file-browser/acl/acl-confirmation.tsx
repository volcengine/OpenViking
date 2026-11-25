import { Button } from '../../../shared/primitives/button/button.js';
import { useEffect, useId, useRef, type RefObject } from 'react';
import { Modal } from '../../../shared/primitives/modal/modal.js';
import type { Locale } from '../../../shared/locale.js';
import type { AclText } from './acl-labels.js';
import { restoreDiff } from './acl-model.js';
import type { AclModel } from './use-acl-report.js';

export function AclConfirmation({
  model,
  text,
  locale,
  triggerRef,
}: {
  model: AclModel;
  text: AclText;
  locale: Locale;
  triggerRef: RefObject<HTMLElement | null>;
}) {
  const cancel = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const restore = model.confirmation?.kind === 'restore';
  const removed = restore && model.report ? restoreDiff(model.report) : [];
  // Focus the safe action in the committed confirmation DOM; the click that
  // requests it runs before the Cancel button exists. No listener needs cleanup;
  // AclModal restores focus to its heading after confirmation removal.
  useEffect(() => {
    cancel.current?.focus();
  }, []);
  const content = (
    <section
      className="ov-file-browser-acl__confirmation"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
    >
      <h3 id={titleId}>{restore ? text.restoreTitle : text.stopTitle}</h3>
      <p id={descriptionId}>
        {restore ? text.restoreDescription : text.stopDescription}
      </p>
      {removed.length ? (
        <>
          <p>{text.restoreRemoved}</p>
          <ul>
            {removed.map((entry) => (
              <li key={entry.principal}>{entry.principal}</li>
            ))}
          </ul>
        </>
      ) : null}
      <div className="ov-file-browser-acl__buttons">
        <Button
          size="small"
          type="default"
          ref={cancel}
          htmlType="button"
          disabled={model.phase === 'saving'}
          onClick={model.cancel}
        >
          {text.cancel}
        </Button>
        <Button
          size="small"
          type="primary"
          htmlType="button"
          className="ov-file-browser-acl__primary"
          disabled={model.phase === 'saving'}
          onClick={model.confirm}
        >
          {model.phase === 'saving' ? text.saving : text.confirm}
        </Button>
      </div>
    </section>
  );
  return restore ? (
    content
  ) : (
    <Modal
      open
      title={text.stopTitle}
      closeLabel={text.cancel}
      triggerRef={triggerRef}
      locale={locale}
      className="ov-file-browser-acl-stop"
      maskClosable={false}
      onClose={model.phase === 'saving' ? undefined : model.cancel}
    >
      {content}
    </Modal>
  );
}
