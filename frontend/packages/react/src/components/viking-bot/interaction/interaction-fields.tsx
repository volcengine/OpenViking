import { Input, TextArea } from '../../../shared/primitives/input/input.js';
import { InteractionField } from './interaction-field.js';
import type { VikingBotInteractionFields } from './field-types.js';
import type { FieldErrors } from './field-validation.js';
import type { ReactNode } from 'react';
import { ChoiceInput } from './choice-input.js';
import { ScheduleInput } from './schedule-input.js';
import type { FormError, FormRequest } from './form-value.js';
import type { InteractionLabels } from './labels.js';
import type { Locale } from '../../../shared/locale.js';
import type { VikingBotInteractionAnswer } from './types.js';

export function InteractionFields({
  request,
  labels,
  locale,
  id,
  answer,
  disabled,
  validation,
  error,
  onChange,
  fields,
  fieldErrors,
}: {
  request: FormRequest;
  labels: InteractionLabels;
  locale: Locale;
  id: string;
  answer?: VikingBotInteractionAnswer;
  disabled: boolean;
  validation?: FormError;
  fields?: VikingBotInteractionFields;
  fieldErrors?: FieldErrors;
  error?: ReactNode;
  onChange: (answer: VikingBotInteractionAnswer) => void;
}) {
  if (request.kind === 'choice' || request.kind === 'repair')
    return (
      <InteractionField
        id={`${id}-answer`}
        error={validation ? labels[validation] : (fieldErrors?.value ?? error)}
        config={fields?.value}
        value={answer?.kind === request.kind ? answer.value : ''}
        disabled={disabled}
        onChange={(value) => onChange({ kind: request.kind, value })}
      >
        {(field) => (
          <ChoiceInput
            controlProps={field}
            request={request}
            labels={labels}
            locale={locale}
            value={answer?.kind === request.kind ? answer.value : undefined}
            disabled={disabled}
            invalid={Boolean(field['aria-invalid'])}
            errorId={field['aria-describedby'] ?? ''}
            onChange={(value) => onChange({ kind: request.kind, value })}
          />
        )}
      </InteractionField>
    );
  if (request.kind === 'schedule')
    return (
      <ScheduleInput
        request={request}
        value={
          answer?.kind === 'schedule' ? answer.value : request.initialValue
        }
        labels={labels}
        locale={locale}
        disabled={disabled}
        invalid={validation}
        fields={fields}
        fieldErrors={fieldErrors}
        onChange={(value) => onChange({ kind: 'schedule', value })}
      />
    );
  const props = {
    name: 'answer',
    value: answer?.kind === 'text' ? answer.value : '',
    disabled,
    required: request.required !== false,
    onChange: (
      event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => onChange({ kind: 'text', value: event.target.value }),
  };
  return (
    <InteractionField
      id={`${id}-answer`}
      label={request.question ?? labels.answer}
      config={fields?.value}
      value={props.value}
      disabled={disabled}
      onChange={(value) => onChange({ kind: 'text', value })}
      description={request.description}
      error={validation ? labels[validation] : (fieldErrors?.value ?? error)}
    >
      {(field) =>
        request.multiline !== false ? (
          <TextArea {...props} {...field} rows={3} />
        ) : (
          <Input {...props} {...field} />
        )
      }
    </InteractionField>
  );
}
