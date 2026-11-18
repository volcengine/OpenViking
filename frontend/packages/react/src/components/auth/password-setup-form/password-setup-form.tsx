import { withMonitoring } from '../../../shared/monitoring/with-monitoring.js';
import { useTrack } from '../../../shared/analytics/use-track.js';
import { useExposure } from '../../../shared/analytics/use-exposure.js';
import { useId } from 'react';
import { Field } from '../../../shared/auth/fields.js';
import { FormFrame } from '../../../shared/auth/form-frame.js';
import { useAuthLabels } from '../../../shared/auth/use-auth-labels.js';
import { useForm } from '../../../shared/auth/use-form.js';
import type { PasswordSetupFormProps } from './types.js';

function PasswordSetupFormView({
  account,
  passwordHint,
  validatePassword,
  onSubmit,
  locale,
  labels: overrides,
  ...options
}: PasswordSetupFormProps) {
  const track = useTrack();
  const exposure = useExposure('password_setup_form_show');
  const id = useId();
  const labels = useAuthLabels(locale, overrides);
  const form = useForm(
    { password: '', confirmation: '' },
    ({ password, confirmation }) => {
      const message = password ? validatePassword?.(password) : undefined;
      return {
        password: !password
          ? { key: 'required' }
          : message === undefined
            ? undefined
            : { message },
        confirmation: !confirmation
          ? { key: 'required' }
          : password !== confirmation
            ? { key: 'mismatch' }
            : undefined,
      };
    },
  );
  return (
    <FormFrame
      {...options}
      formRef={exposure}
      labels={labels}
      submitLabel={labels.confirm}
      validate={form.validate}
      onSubmit={() => {
        track('password_setup_form_submit');
        onSubmit?.({ password: form.values.password });
      }}
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

export const PasswordSetupForm = withMonitoring(
  PasswordSetupFormView,
  'PasswordSetupForm',
);
