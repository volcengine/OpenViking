import { useId } from 'react';
import { Field } from '../../shared/auth/fields.js';
import { FormFrame } from '../../shared/auth/form-frame.js';
import { useAuthLabels } from '../../shared/auth/use-auth-labels.js';
import { useForm } from '../../shared/auth/use-form.js';
import type { ApiKeyLoginFormProps } from './types.js';

export function ApiKeyLoginForm({
  alternateLogin,
  onSubmit,
  locale,
  labels: overrides,
  ...options
}: ApiKeyLoginFormProps) {
  const id = useId();
  const labels = useAuthLabels(locale, overrides);
  const form = useForm({ apiKey: '' }, ({ apiKey }) => ({
    apiKey: apiKey.trim() ? undefined : { key: 'required' },
  }));
  return (
    <FormFrame
      {...options}
      labels={labels}
      submitLabel={labels.login}
      validate={form.validate}
      secondaryAction={alternateLogin}
      onSubmit={() => onSubmit?.({ apiKey: form.values.apiKey })}
    >
      <Field
        id={`${id}-key`}
        label={labels.apiKey}
        labels={labels}
        value={form.values.apiKey}
        secret
        autoComplete="off"
        error={form.errors.apiKey}
        onChange={(value) => form.update('apiKey', value)}
      />
    </FormFrame>
  );
}
