import { useId, useRef, useState } from 'react';
import { Button } from '../../../shared/primitives/button/button.js';
import { ChevronRightIcon, FolderIcon } from '../../../icons/index.js';
import { DirectoryPickerTrigger } from './directory-picker.js';
import { DirectoryPermission } from './directory-permission.js';
import {
  DirectoryFlowError,
  interactionError,
  resolveDirectoryPermission,
} from './directory-state.js';
import type { VikingBotInteractionTargetRequest } from './directory-picker-types.js';
import type { VikingBotInteractionProps } from './types.js';
import type { InteractionLabels } from './labels.js';
import type { Locale } from '../../../shared/locale.js';

export function TargetInteraction({
  request,
  labels,
  locale,
  disabled,
  submitting,
  error: hostError,
  onSubmit,
  onCancel,
  onPrevious,
}: Omit<VikingBotInteractionProps, 'request' | 'labels' | 'locale'> & {
  request: VikingBotInteractionTargetRequest;
  labels: InteractionLabels;
  locale: Locale;
  onPrevious?: () => void;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const busy = useRef(false);
  const [uri, setUri] = useState(request.initialUri ?? '');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{
    code: 'submitFailed';
    cause: unknown;
  }>();
  const blocked = Boolean(disabled || submitting || pending);
  const missingUri = !uri.trim();
  const permission = resolveDirectoryPermission(
    uri,
    request.directoryPicker.permission,
  );
  const submit = async () => {
    if (blocked || busy.current) return;
    if (missingUri) {
      setError({
        code: 'submitFailed',
        cause: new DirectoryFlowError('directoryUnavailable'),
      });
      trigger.current?.focus();
      return;
    }
    busy.current = true;
    setPending(true);
    setError(undefined);
    try {
      await onSubmit?.({ kind: 'target', value: uri });
    } catch (cause) {
      if (trigger.current) setError({ code: 'submitFailed', cause });
    } finally {
      busy.current = false;
      if (trigger.current) setPending(false);
    }
  };
  const failure =
    hostError ??
    (error ? interactionError(error.cause, labels, error.code) : undefined);
  return (
    <>
      <form
        noValidate
        aria-labelledby={`${id}-title`}
        aria-busy={pending || submitting || undefined}
        className="ov-viking-bot__interaction-card"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <h3
          tabIndex={-1}
          id={`${id}-title`}
          className="ov-viking-bot__interaction-title"
        >
          {request.question ?? labels.directoryTarget}
        </h3>
        <div className="ov-viking-bot__interaction-fields">
          <label
            id={`${id}-label`}
            htmlFor={`${id}-directory`}
            className="ov-viking-bot__interaction-label"
          >
            {labels.directoryTarget}
          </label>
          <DirectoryPickerTrigger
            key={String(blocked)}
            picker={request.directoryPicker}
            value={uri}
            labels={labels}
            locale={locale}
            triggerRef={trigger}
            disabled={blocked}
            onConfirm={(value) => {
              request.directoryPicker.onSelectionChange?.(value);
              setUri(value);
              setError(undefined);
            }}
          >
            {(open, show) => (
              <Button
                ref={trigger}
                id={`${id}-directory`}
                type="outline"
                className="ov-viking-bot__directory-trigger"
                aria-labelledby={`${id}-label`}
                aria-haspopup="dialog"
                aria-expanded={open}
                aria-invalid={Boolean(failure)}
                aria-describedby={`${id}-status${failure ? ` ${id}-error` : ''}`}
                disabled={blocked}
                onClick={show}
              >
                <FolderIcon width={14} height={14} />
                <span title={uri}>{uri || labels.directoryPickerHint}</span>
                <ChevronRightIcon width={18} height={18} />
              </Button>
            )}
          </DirectoryPickerTrigger>
          <div id={`${id}-status`}>
            <DirectoryPermission permission={permission} labels={labels} />
          </div>
          {failure ? (
            <p
              id={`${id}-error`}
              role="alert"
              className="ov-viking-bot__interaction-error"
            >
              {failure}
            </p>
          ) : null}
        </div>
        <div className="ov-viking-bot__interaction-footer">
          {onPrevious ? (
            <Button type="outline" disabled={blocked} onClick={onPrevious}>
              {labels.previous}
            </Button>
          ) : null}
          {onCancel ? (
            <Button
              type="outline"
              disabled={blocked}
              onClick={() => {
                request.directoryPicker.onSelectionChange?.(
                  request.initialUri ?? '',
                );
                setUri(request.initialUri ?? '');
                setError(undefined);
                onCancel();
              }}
            >
              {labels.cancel}
            </Button>
          ) : null}
          <Button
            type="primary"
            htmlType="submit"
            loading={pending || submitting}
            disabled={blocked || missingUri}
          >
            {labels.submit}
          </Button>
        </div>
      </form>
    </>
  );
}
