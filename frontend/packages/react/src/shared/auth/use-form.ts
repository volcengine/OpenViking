import { useState } from 'react';
import type { AuthIssue } from './types.js';

export function useForm<T extends Record<string, string>>(
  initial: T,
  validate: (values: T) => Partial<Record<keyof T, AuthIssue>>,
) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<Partial<Record<keyof T, AuthIssue>>>({});
  const update = (key: keyof T, value: string) => {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  };
  return {
    values,
    errors,
    update,
    validate: () => {
      const next = validate(values);
      setErrors(next);
      return !Object.values(next).some((issue) => issue !== undefined);
    },
  };
}
export const validEmail = (value: string) =>
  /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
