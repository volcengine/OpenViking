import {
  useEffect,
  useId,
  useCallback,
  useRef,
  useState,
  type RefObject,
} from 'react';
import { Modal } from '../../../shared/primitives/modal/modal.js';
import { Button } from '../../../shared/primitives/button/button.js';
import { Input } from '../../../shared/primitives/input/input.js';
import type { Locale } from '../../../shared/locale.js';
import type { InteractionLabels } from './labels.js';
import type { VikingBotInteractionDirectoryOperations } from './directory-picker-types.js';
import { childDirectoryUri, directoryError } from './directory-state.js';

export function CreateDirectory({
  parentUri,
  operations,
  labels,
  locale,
  triggerRef,
  onClose,
  onCreated,
}: {
  parentUri: string;
  operations: VikingBotInteractionDirectoryOperations;
  labels: InteractionLabels;
  locale: Locale;
  triggerRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onCreated: (uri: string) => void;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const focusInput = useCallback((node: HTMLInputElement | null) => {
    input.current = node;
    node?.focus();
  }, []);
  const [name, setName] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  // Cancel the host operation on unmount; the controller belongs to the event-created operation.
  // Effect cleanup prevents a late mutation result from updating a closed form.
  useEffect(() => () => controller.current?.abort(), []);
  const create = async () => {
    if (controller.current || !operations.createDirectory) return;
    try {
      childDirectoryUri(parentUri, name);
    } catch (cause) {
      setError(cause);
      input.current?.focus();
      return;
    }
    const abort = new AbortController();
    controller.current = abort;
    setPending(true);
    setError(undefined);
    try {
      const uri = await operations.createDirectory(parentUri, name, {
        signal: abort.signal,
      });
      if (abort.signal.aborted) return;
      onCreated(uri);
      // A failed refresh cannot turn an acknowledged creation into a failed mutation.
      void operations.refresh?.(parentUri).catch(() => {});
    } catch (cause) {
      if (!abort.signal.aborted) setError(cause);
    } finally {
      if (!abort.signal.aborted) {
        controller.current = null;
        setPending(false);
      }
    }
  };
  return (
    <Modal
      open
      title={labels.directoryCreateTitle}
      triggerRef={triggerRef}
      locale={locale}
      onClose={pending ? undefined : onClose}
      className="ov-viking-bot__create-directory"
    >
      <form
        noValidate
        aria-labelledby={`${id}-title`}
        aria-busy={pending || undefined}
        onSubmit={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void create();
        }}
      >
        <h2
          id={`${id}-title`}
          className="ov-viking-bot__create-directory-title"
        >
          {labels.directoryCreateTitle}
        </h2>
        <p className="ov-viking-bot__directory-status">
          {labels.directoryParent(parentUri)}
        </p>
        <label
          className="ov-viking-bot__interaction-label"
          htmlFor={`${id}-name`}
        >
          {labels.directoryName}
        </label>
        <Input
          ref={focusInput}
          id={`${id}-name`}
          name="directory-name"
          value={name}
          required
          disabled={pending}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="none"
          spellCheck={false}
          aria-invalid={Boolean(error)}
          aria-describedby={`${id}-hint${error ? ` ${id}-error` : ''}`}
          onChange={(event) => {
            setName(event.target.value);
            setError(undefined);
          }}
        />
        <p id={`${id}-hint`} className="ov-viking-bot__directory-status">
          {labels.directoryNameHint}
        </p>
        {error ? (
          <p
            id={`${id}-error`}
            role="alert"
            className="ov-viking-bot__directory-status ov-viking-bot__directory-status--error"
          >
            {directoryError(error, labels, 'directoryCreateFailed')}
          </p>
        ) : null}
        <div className="ov-viking-bot__create-directory-actions">
          <Button type="outline" disabled={pending} onClick={onClose}>
            {labels.cancel}
          </Button>
          <Button
            type="primary"
            htmlType="submit"
            disabled={pending}
            loading={pending}
          >
            {labels.directoryCreateConfirm}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
