import { useId } from 'react';
import { Field } from '../../shared/auth/fields.js';
import { FormFrame } from '../../shared/auth/form-frame.js';
import { defaultLabels } from '../../shared/auth/types.js';
import { useForm } from '../../shared/auth/use-form.js';
import type { PasswordSetupFormProps } from './types.js';

export function PasswordSetupForm({
  account,
  passwordHint,
  validatePassword,
  onSubmit,
  ...options
}: PasswordSetupFormProps) {
  const id = useId();
  const labels = { ...defaultLabels, ...options.labels };
  const form = useForm(
    { password: '', confirmation: '' },
    ({ password, confirmation }) => ({
      password: !password ? labels.required : validatePassword?.(password),
      confirmation: !confirmation
        ? labels.required
        : password !== confirmation
          ? labels.mismatch
          : undefined,
    }),
  );
  return (
    <FormFrame
      {...options}
      labels={labels}
      submitLabel={labels.confirm}
      validate={form.validate}
      onSubmit={() => onSubmit?.({ password: form.values.password })}
    >
      {account && (
        <div className="ov-auth__account">
          <strong>{account}</strong>
          <span>{labels.account}</span>
        </div>
      )}
      <Field
        id={`${id}-password`}
        label={labels.newPassword}
        labels={labels}
        value={form.values.password}
        secret
        autoComplete="new-password"
        aria-describedby={`${id}-hint`}
        error={form.errors.password}
        onChange={(value) => form.update('password', value)}
      />
      <Field
        id={`${id}-confirmation`}
        label={labels.confirmation}
        labels={labels}
        value={form.values.confirmation}
        secret
        autoComplete="new-password"
        aria-describedby={`${id}-hint`}
        error={form.errors.confirmation}
        onChange={(value) => form.update('confirmation', value)}
      />
      <p className="ov-auth__description" id={`${id}-hint`}>
        {passwordHint}
      </p>
    </FormFrame>
  );
}
