import { useEffect, useId, useRef, useState, type RefObject } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import { Input } from '../../shared/primitives/input/input.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import { FormField } from '../../shared/primitives/form-field/form-field.js';
import type { Locale } from '../../shared/locale.js';
import type { VikingBotSession, VikingBotSessions } from './capabilities.js';
import type { VikingBotLabels } from './model.js';

export function SessionHistoryDialog({
  kind,
  session,
  sessions,
  labels,
  locale,
  triggerRef,
  onClose,
}: {
  kind: 'rename' | 'remove';
  session: VikingBotSession;
  sessions: VikingBotSessions;
  labels: Required<VikingBotLabels>;
  locale: Locale;
  triggerRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
}) {
  const id = useId();
  const renaming = kind === 'rename';
  const heading = renaming ? labels.renameSession : labels.removeSession;
  const [name, setName] = useState(session.title);
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const restoreTarget = useRef<HTMLButtonElement | null>(null);
  // Menu items may unmount before FocusLock can return focus to them. After its
  // cleanup, restore the original history trigger only if focus fell to the body.
  useEffect(
    () => () => {
      const target = restoreTarget.current;
      if (!target) return;
      const { ownerDocument } = target;
      ownerDocument.defaultView?.requestAnimationFrame(() => {
        if (
          triggerRef.current === target &&
          target.isConnected &&
          ownerDocument.activeElement === ownerDocument.body
        ) {
          target.focus({ preventScroll: true });
        }
      });
    },
    [triggerRef],
  );
  const close = () => {
    restoreTarget.current = triggerRef.current;
    onClose();
  };
  const [failure, setFailure] = useState<{
    kind: 'required' | 'request';
    message?: string;
  }>();
  const submit = async () => {
    if (busy.current) return;
    const value = name.trim();
    if (renaming && !value) {
      setFailure({ kind: 'required' });
      return;
    }
    busy.current = true;
    setPending(true);
    setFailure(undefined);
    try {
      const result = renaming
        ? await sessions.onRename?.(session.id, value)
        : await sessions.onRemove?.(session.id);
      if (result === false) setFailure({ kind: 'request' });
      else close();
    } catch (cause) {
      setFailure({
        kind: 'request',
        message: cause instanceof Error ? cause.message : undefined,
      });
    } finally {
      busy.current = false;
      setPending(false);
    }
  };
  const error =
    failure?.kind === 'required'
      ? labels.requiredSessionName
      : failure
        ? failure.message ||
          (renaming ? labels.renameSessionFailed : labels.removeSessionFailed)
        : undefined;
  return (
    <Modal
      open
      title={heading}
      triggerRef={triggerRef}
      locale={locale}
      onClose={pending ? undefined : close}
      className="ov-viking-bot__session-dialog"
    >
      <form
        noValidate
        aria-labelledby={id}
        aria-busy={pending || undefined}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <h2 id={id}>{heading}</h2>
        {renaming ? (
          <FormField id={`${id}-name`} label={labels.sessionName} error={error}>
            {(field) => (
              <Input
                {...field}
                data-autofocus
                name="session-name"
                value={name}
                maxLength={80}
                required
                disabled={pending}
                onChange={(event) => {
                  setName(event.target.value);
                  setFailure(undefined);
                }}
              />
            )}
          </FormField>
        ) : (
          <>
            <p className="ov-viking-bot__session-dialog-name">
              {session.title}
            </p>
            <p>{labels.sessionRemovalNotice}</p>
            {error ? <p role="alert">{error}</p> : null}
          </>
        )}
        <div className="ov-viking-bot__session-dialog-actions">
          <Button
            type="outline"
            disabled={pending}
            onClick={close}
            data-autofocus={!renaming || undefined}
          >
            {labels.cancelSessionAction}
          </Button>
          <Button
            type="primary"
            htmlType="submit"
            loading={pending}
            className={
              renaming ? undefined : 'ov-viking-bot__session-confirm-delete'
            }
          >
            {renaming ? labels.saveSessionName : labels.confirmSessionRemoval}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
