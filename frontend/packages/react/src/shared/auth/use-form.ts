import { useState } from 'react';

export function useForm<T extends Record<string, string>>(
  initial: T,
  validate: (values: T) => Partial<Record<keyof T, string>>,
) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<Partial<Record<keyof T, string>>>({});
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
      return !Object.values(next).some(Boolean);
    },
  };
}
export const validEmail = (value: string) =>
  /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
