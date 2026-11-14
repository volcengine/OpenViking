import { ErrorNotification } from '../../../shared/feedback/error-notification.js';
import { useToast } from '../../../shared/primitives/toast/use-toast.js';
import { useId, useRef, useState } from 'react';
import { Button } from '../../../shared/primitives/button/button.js';
import { interactionError } from './directory-state.js';
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
  const composing = useRef(false);
  const inFlight = useRef(false);
  const [initial] = useState(() => initialAnswer(request));
  const [answer, setAnswer] = useState(initial);
  const [resetCount, setResetCount] = useState(0);
  const [pending, setPending] = useState(false);
  const [validation, setValidation] = useState<FormError>();
  const [submitError, setSubmitError] = useState<{ cause: unknown }>();
  const busy = Boolean(submitting || pending);
  const blocked = Boolean(disabled || busy);
  const options = request.kind === 'choice' || request.kind === 'repair';
  const change = (value: VikingBotInteractionAnswer) => {
    if (blocked) return;
    setAnswer(value);
    toast.clear();
    setValidation(undefined);
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
        content:
          options && invalid === 'required'
            ? labels.invalidAnswer
            : invalid
              ? labels[invalid]
              : labels.required,
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
      await onSubmit?.(answer);
    } catch (cause) {
      setSubmitError({ cause });
    } finally {
      inFlight.current = false;
      setPending(false);
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
          onChange={change}
        />
        {validation ? (
          <p
            id={`${id}-error`}
            className="ov-viking-bot__interaction-error-description"
          >
            {labels[validation]}
          </p>
        ) : null}
        {failure ? (
          <ErrorNotification
            title={labels.submitFailed}
            error={submitError?.cause}
            identity={error ?? submitError}
            locale={locale}
          >
            {failure}
          </ErrorNotification>
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
            className="ov-viking-bot__interaction-action"
            disabled={blocked}
            onClick={() => {
              setAnswer(initial);
              toast.clear();
              setValidation(undefined);
              setSubmitError(undefined);
              setResetCount((count) => count + 1);
              onCancel();
            }}
          >
            {labels.cancel}
          </Button>
        ) : null}
        <Button
          type="primary"
          htmlType="submit"
          className="ov-viking-bot__interaction-action"
          loading={busy}
          disabled={blocked || Boolean(validateAnswer(request, answer))}
        >
          {labels.submit}
        </Button>
      </div>
    </form>
  );
}
