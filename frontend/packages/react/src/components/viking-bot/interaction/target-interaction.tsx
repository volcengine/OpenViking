import { useId, useRef, useState } from 'react';
import { ErrorNotification } from '../../../shared/feedback/error-notification.js';
import { useToast } from '../../../shared/primitives/toast/use-toast.js';
import { useValidationLifetime } from './use-validation-lifetime.js';
import { Button } from '../../../shared/primitives/button/button.js';
import { focusError } from './focus-error.js';
import { InteractionActions } from './interaction-actions.js';
import { InteractionField } from './interaction-field.js';
import { validateFields } from './field-validation.js';
import type { ReactNode } from 'react';
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
  fields,
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
  const [toast, toastHolder] = useToast({ locale });
  const { validationRequest, mounted } = useValidationLifetime();
  const formRef = useRef<HTMLFormElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const busy = useRef(false);
  const [uri, setUri] = useState(request.initialUri ?? '');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{
    code: 'submitFailed';
    cause: unknown;
  }>();
  const [fieldError, setFieldError] = useState<ReactNode>();
  const change = (value: string) => {
    request.directoryPicker.onSelectionChange?.(value);
    setUri(value);
    toast.clear();
    setError(undefined);
    setFieldError(undefined);
  };
  const blocked = Boolean(disabled || submitting || pending);
  const permission = resolveDirectoryPermission(
    uri,
    request.directoryPicker.permission,
  );
  const submit = async () => {
    if (blocked || busy.current) return;
    if (!uri.trim()) {
      toast.error({
        id: 'target-directory-required',
        content: labels.directoryUnavailable,
      });
      setError({
        code: 'submitFailed',
        cause: new DirectoryFlowError('directoryUnavailable'),
      });
      trigger.current?.focus();
      return;
    }
    toast.clear();
    busy.current = true;
    setPending(true);
    setError(undefined);
    try {
      const answer = { kind: 'target', value: uri } as const;
      const errors = await validateFields(
        fields,
        answer,
        validationRequest.current.signal,
      );
      if (!mounted.current) return;
      setFieldError(errors.value);
      if (errors.value) {
        window.requestAnimationFrame(() => focusError(formRef.current));
        return;
      }
      await onSubmit?.(answer);
    } catch (cause) {
      if (mounted.current) setError({ code: 'submitFailed', cause });
    } finally {
      busy.current = false;
      if (mounted.current) setPending(false);
    }
  };
  const failure =
    hostError ??
    (error ? interactionError(error.cause, labels, error.code) : undefined);
  return (
    <>
      {toastHolder}
      <form
        ref={formRef}
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
          <InteractionField
            id={`${id}-directory`}
            label={labels.directoryTarget}
            description={
              <DirectoryPermission permission={permission} labels={labels} />
            }
            error={fieldError ?? failure}
            config={fields?.value}
            value={uri}
            disabled={blocked}
            onChange={change}
          >
            {(field) => (
              <DirectoryPickerTrigger
                key={String(blocked)}
                picker={request.directoryPicker}
                value={uri}
                labels={labels}
                locale={locale}
                triggerRef={trigger}
                disabled={blocked}
                onConfirm={change}
              >
                {(open, show) => (
                  <Button
                    ref={trigger}
                    {...field}
                    type="outline"
                    className="ov-viking-bot__directory-trigger"
                    aria-haspopup="dialog"
                    aria-expanded={open}
                    disabled={blocked}
                    onClick={show}
                  >
                    <FolderIcon width={14} height={14} />
                    <span title={uri}>{uri || labels.directoryPickerHint}</span>
                    <ChevronRightIcon width={18} height={18} />
                  </Button>
                )}
              </DirectoryPickerTrigger>
            )}
          </InteractionField>
          {failure && !(error?.cause instanceof DirectoryFlowError) ? (
            <ErrorNotification
              title={labels.submitFailed}
              error={error?.cause}
              identity={hostError ?? error}
              locale={locale}
            >
              {failure}
            </ErrorNotification>
          ) : null}
        </div>
        <InteractionActions
          labels={labels}
          onPrevious={onPrevious}
          submitDisabled={!uri.trim()}
          disabled={blocked}
          submitting={Boolean(pending || submitting)}
          onCancel={
            onCancel
              ? () => {
                  change(request.initialUri ?? '');
                  onCancel();
                }
              : undefined
          }
        />
      </form>
    </>
  );
}
