import { useTrack } from '../../../shared/analytics/use-track.js';
import { useExposure } from '../../../shared/analytics/use-exposure.js';
import { useId } from 'react';
import { Field } from '../../../shared/auth/fields.js';
import { FormFrame } from '../../../shared/auth/form-frame.js';
import { useAuthLabels } from '../../../shared/auth/use-auth-labels.js';
import { useForm, validEmail } from '../../../shared/auth/use-form.js';
import type { PasswordResetRequestFormProps } from './types.js';

export function PasswordResetRequestForm({
  defaultEmail = '',
  sentMessage,
  retryAfter = 0,
  onSubmit,
  locale,
  labels: overrides,
  ...options
}: PasswordResetRequestFormProps) {
  const track = useTrack();
  const exposure = useExposure('password_reset_request_form_show');
  const id = useId();
  const labels = useAuthLabels(locale, overrides);
  const form = useForm({ email: defaultEmail }, ({ email }) => ({
    email: validEmail(email) ? undefined : { key: 'invalidEmail' },
  }));
  const [sentEmail, message] = sentMessage
    ? [sentMessage.email, sentMessage.message]
    : [];
  const sent = sentEmail === form.values.email.trim();
  const remaining = Math.max(0, Math.ceil(retryAfter));
  return (
    <FormFrame
      {...options}
      formRef={exposure}
      disabled={options.disabled || remaining > 0}
      labels={labels}
      submitLabel={`${sent ? labels.resend : labels.send}${remaining > 0 ? ` (${remaining}s)` : ''}`}
      validate={form.validate}
      onSubmit={() => {
        track('password_reset_request_form_submit');
        onSubmit?.({ email: form.values.email.trim() });
      }}
    >
      <Field
        id={`${id}-email`}
        label={labels.email}
        labels={labels}
        value={form.values.email}
        type="email"
        autoComplete="email"
        error={form.errors.email}
        onChange={(value) => form.update('email', value)}
      />
      {sent && message && <p role="status">{message}</p>}
    </FormFrame>
  );
}
