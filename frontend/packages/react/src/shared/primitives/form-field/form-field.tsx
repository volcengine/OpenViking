import type { ReactNode } from 'react';

interface FormFieldControlProps {
  id: string;
  'aria-labelledby'?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: true;
}

/** Field presentation only; values, validation and error clearing belong to the caller. */
export function FormField({
  id,
  label,
  description,
  error,
  invalid = Boolean(error),
  group = false,
  children,
}: {
  id: string;
  label?: ReactNode;
  description?: ReactNode;
  error?: ReactNode;
  invalid?: boolean;
  group?: boolean;
  children: (control: FormFieldControlProps) => ReactNode;
}) {
  const Field = group ? 'fieldset' : 'div';
  const Label = group ? 'legend' : 'label';
  const describedBy =
    [description ? `${id}-description` : '', invalid ? `${id}-error` : '']
      .filter(Boolean)
      .join(' ') || undefined;
  return (
    <Field className={`ov-form-field${invalid ? ' ov-form-field--error' : ''}`}>
      {label != null ? (
        <Label
          id={`${id}-label`}
          htmlFor={group ? undefined : id}
          className="ov-form-field__label"
        >
          {label}
        </Label>
      ) : null}
      <div className="ov-form-field__control">
        {children({
          id,
          'aria-labelledby': label != null ? `${id}-label` : undefined,
          'aria-describedby': describedBy,
          'aria-invalid': invalid || undefined,
        })}
      </div>
      {invalid ? (
        <div id={`${id}-error`} role="alert" className="ov-form-field__error">
          {error}
        </div>
      ) : null}
      {description ? (
        <div id={`${id}-description`} className="ov-form-field__description">
          {description}
        </div>
      ) : null}
    </Field>
  );
}
