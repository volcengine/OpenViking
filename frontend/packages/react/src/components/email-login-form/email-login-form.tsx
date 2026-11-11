import { useId } from 'react';
import { Field } from '../../shared/auth/fields.js';
import { FormFrame } from '../../shared/auth/form-frame.js';
import { useAuthLabels } from '../../shared/auth/use-auth-labels.js';
import { useForm, validEmail } from '../../shared/auth/use-form.js';
import type { EmailLoginFormProps } from './types.js';

export function EmailLoginForm({
  defaultEmail = '',
  readOnlyEmail,
  forgotPassword,
  alternateLogin,
  onSubmit,
  locale,
  labels: overrides,
  ...options
}: EmailLoginFormProps) {
  const id = useId();
  const labels = useAuthLabels(locale, overrides);
  const form = useForm({ email: defaultEmail, password: '' }, (values) => ({
    email: validEmail(values.email) ? undefined : { key: 'invalidEmail' },
    password: values.password ? undefined : { key: 'required' },
  }));
  return (
    <FormFrame
      {...options}
      labels={labels}
      submitLabel={labels.login}
      validate={form.validate}
      secondaryAction={alternateLogin}
      onSubmit={() =>
        onSubmit?.({ ...form.values, email: form.values.email.trim() })
      }
    >
      <Field
        id={`${id}-email`}
        label={labels.email}
        labels={labels}
        value={form.values.email}
        readOnly={readOnlyEmail}
        type="email"
        autoComplete="username"
        error={form.errors.email}
        onChange={(value) => form.update('email', value)}
      />
      <Field
        id={`${id}-password`}
        label={labels.password}
        labels={labels}
        value={form.values.password}
        secret
        autoComplete="current-password"
        action={forgotPassword}
        error={form.errors.password}
        onChange={(value) => form.update('password', value)}
      />
    </FormFrame>
  );
}
