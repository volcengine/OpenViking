import { useId, useRef, useState } from 'react';
import { ErrorNotification } from '../../../shared/feedback/error-notification.js';
import { useToast } from '../../../shared/primitives/toast/use-toast.js';
import { useValidationLifetime } from './use-validation-lifetime.js';
import { focusError } from './focus-error.js';
import { InteractionActions } from './interaction-actions.js';
import { interactionError } from './directory-state.js';
import {
  validateFields,
  clearChangedErrors,
  clearChangedValidation,
  type FieldErrors,
} from './field-validation.js';
import { InteractionFields } from './interaction-fields.js';
import {
  initialAnswer,
  validateAnswer,
  type FormError,
  type FormRequest,
} from './form-value.js';
import type { InteractionLabels } from './labels.js';
import type { Locale } from '../../../shared/locale.js';
import type {
  VikingBotInteractionProps,
  VikingBotInteractionAnswer,
} from './types.js';

export function InteractionForm({
  request,
  labels,
  locale,
  disabled,
  submitting,
  error,
  fields,
  onSubmit,
  onCancel,
  onPrevious,
}: Omit<VikingBotInteractionProps, 'request' | 'labels' | 'locale'> & {
  request: FormRequest;
  labels: InteractionLabels;
  locale: Locale;
  onPrevious?: () => void;
}) {
  const id = useId();
  const [toast, toastHolder] = useToast({ locale });
  const formRef = useRef<HTMLFormElement>(null);
  const { validationRequest, mounted } = useValidationLifetime();
  const composing = useRef(false);
  const inFlight = useRef(false);
  const [initial] = useState(() => initialAnswer(request));
  const [answer, setAnswer] = useState(initial);
  const [resetCount, setResetCount] = useState(0);
  const [pending, setPending] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [validation, setValidation] = useState<FormError>();
  const [submitError, setSubmitError] = useState<{ cause: unknown }>();
  const busy = Boolean(submitting || pending);
  const blocked = Boolean(disabled || busy);
  const options = request.kind === 'choice' || request.kind === 'repair';
  const change = (value: VikingBotInteractionAnswer) => {
    if (blocked) return;
    setFieldErrors((errors) => clearChangedErrors(errors, answer, value));
    setAnswer(value);
    toast.clear();
    setValidation((previous) =>
      clearChangedValidation(previous, answer, value),
    );
    setSubmitError(undefined);
  };
  const failure =
    error ??
    (submitError
      ? interactionError(submitError.cause, labels, 'submitFailed')
      : undefined);
  const submit = async () => {
    if (blocked || inFlight.current || composing.current) return;
    const invalid = validateAnswer(request, answer);
    setValidation(invalid);
    if (invalid || !answer) {
      toast.warning({
        id: 'validation',
        content: invalid ? labels[invalid] : labels.required,
      });
      const selector =
        invalid === 'timeInvalid'
          ? '[name="schedule-time"]'
          : invalid === 'weekdayRequired'
            ? '[role="combobox"]'
            : formRef.current?.querySelector('[name="custom-answer"]')
              ? '[name="custom-answer"]'
              : 'input:not(:disabled), .ov-viking-bot__interaction-picker-trigger:not(:disabled)';
      formRef.current?.querySelector<HTMLElement>(selector)?.focus();
      return;
    }
    toast.clear();
    inFlight.current = true;
    setPending(true);
    setSubmitError(undefined);
    try {
      const errors = await validateFields(
        fields,
        answer,
        validationRequest.current.signal,
      );
      if (!mounted.current) return;
      setFieldErrors(errors);
      if (Object.values(errors).some(Boolean)) {
        window.requestAnimationFrame(() => focusError(formRef.current));
        return;
      }
      await onSubmit?.(answer);
    } catch (cause) {
      if (mounted.current) setSubmitError({ cause });
    } finally {
      inFlight.current = false;
      if (mounted.current) setPending(false);
    }
  };
  return (
    <form
      ref={formRef}
      className={`ov-viking-bot__interaction-card${options ? ' ov-viking-bot__interaction-card--options' : ''}`}
      aria-labelledby={`${id}-title`}
      aria-busy={busy || undefined}
      noValidate
      onCompositionStart={() => {
        composing.current = true;
      }}
      onCompositionEnd={() => {
        composing.current = false;
      }}
      onKeyDownCapture={(event) => {
        if (
          event.key === 'Enter' &&
          (composing.current ||
            event.nativeEvent.isComposing ||
            event.nativeEvent.keyCode === 229)
        )
          event.preventDefault();
      }}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      {toastHolder}
      <h3
        tabIndex={-1}
        id={`${id}-title`}
        className="ov-viking-bot__interaction-title"
      >
        {request.question ?? labels.title}
      </h3>
      <div className="ov-viking-bot__interaction-fields">
        <InteractionFields
          key={resetCount}
          request={request}
          labels={labels}
          locale={locale}
          id={id}
          answer={answer}
          disabled={blocked}
          validation={validation}
          fields={fields}
          fieldErrors={fieldErrors}
          error={failure}
          onChange={change}
        />
        {failure ? (
          <ErrorNotification
            title={labels.submitFailed}
            error={submitError?.cause}
            identity={error ?? submitError}
          >
            {failure}
          </ErrorNotification>
        ) : null}
      </div>
      <InteractionActions
        labels={labels}
        onPrevious={onPrevious}
        submitDisabled={Boolean(validateAnswer(request, answer))}
        disabled={blocked}
        submitting={busy}
        onCancel={
          onCancel
            ? () => {
                setFieldErrors({});
                setAnswer(initial);
                setValidation(undefined);
                setSubmitError(undefined);
                setResetCount((count) => count + 1);
                onCancel();
              }
            : undefined
        }
      />
    </form>
  );
}
