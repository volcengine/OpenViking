import { useState } from 'react';
import type { InputHTMLAttributes } from 'react';
import type { AuthAction, AuthIssue, AuthLabels } from './types.js';

export function Action({ action }: { action?: AuthAction }) {
  return action?.onClick ? (
    <button className="ov-auth__link" type="button" onClick={action.onClick}>
      {action.label}
    </button>
  ) : null;
}
interface FieldProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'onChange'
> {
  label: string;
  error?: AuthIssue;
  secret?: boolean;
  labels: AuthLabels;
  action?: AuthAction;
  onChange: (value: string) => void;
}
export function Field({
  label,
  error,
  secret,
  labels,
  action,
  onChange,
  id,
  ...input
}: FieldProps) {
  const [visible, setVisible] = useState(false);
  const message = error && ('key' in error ? labels[error.key] : error.message);
  return (
    <div className="ov-auth__field">
      <div className="ov-auth__label">
        <label htmlFor={id}>{label}</label>
        <Action action={action} />
      </div>
      <div className="ov-auth__input-wrap">
        <input
          {...input}
          id={id}
          onChange={(event) => onChange(event.target.value)}
          type={secret ? (visible ? 'text' : 'password') : input.type}
          aria-invalid={error !== undefined}
          aria-describedby={message ? `${id}-error` : input['aria-describedby']}
        />
        {secret && (
          <button
            type="button"
            className="ov-auth__visibility"
            disabled={input.disabled}
            aria-label={visible ? labels.hidePassword : labels.showPassword}
            aria-pressed={visible}
            onClick={() => setVisible(!visible)}
          >
            <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
              {visible ? (
                <>
                  <path d="M1.5 8s2.2-4 6.5-4 6.5 4 6.5 4-2.2 4-6.5 4-6.5-4-6.5-4Z" />
                  <circle cx="8" cy="8" r="2" />
                </>
              ) : (
                <path d="M2 6.25C3.5 8.35 5.5 9.5 8 9.5s4.5-1.15 6-3.25M4.3 8.45l-1.1 1.8M8 9.5v2M11.7 8.45l1.1 1.8" />
              )}
            </svg>
          </button>
        )}
      </div>
      {message && (
        <p className="ov-auth__error" id={`${id}-error`} role="alert">
          {message}
        </p>
      )}
    </div>
  );
}
