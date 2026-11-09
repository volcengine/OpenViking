import { useState } from 'react';
import type { ReactNode } from 'react';
import { Action } from './fields.js';
import { AgreementDialog, AgreementLinks } from './agreements.js';
import type { AuthAction, AuthFormOptions, AuthLabels } from './types.js';

interface Props extends AuthFormOptions {
  children: ReactNode;
  labels: AuthLabels;
  submitLabel: string;
  validate: () => boolean;
  onSubmit: () => void;
  secondaryAction?: AuthAction;
}
export function FormFrame({
  children,
  labels,
  submitLabel,
  validate,
  onSubmit,
  secondaryAction,
  ...options
}: Props) {
  const [accepted, setAccepted] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const agreements = options.agreements?.items.length
    ? options.agreements
    : undefined;
  const blocked = options.loading || options.disabled;
  const submit = (agreed = false) => {
    if (blocked || !validate()) return;
    if (agreements && !accepted && !agreed) {
      setConfirming(true);
      return;
    }
    if (agreed) setAccepted(true);
    onSubmit();
  };
  return (
    <form
      className={`ov-auth ${options.className ?? ''}`}
      noValidate
      aria-busy={options.loading || undefined}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Action action={options.back} />
      {options.title && <h2>{options.title}</h2>}
      {options.description && (
        <p className="ov-auth__description">{options.description}</p>
      )}
      <fieldset disabled={blocked}>{children}</fieldset>
      {options.error && (
        <div className="ov-auth__error" role="alert">
          {options.error}
        </div>
      )}
      <button
        className="ov-auth__primary ov-auth__submit"
        type="submit"
        disabled={blocked}
      >
        {submitLabel}
        {options.loading && <span aria-hidden="true"> …</span>}
      </button>
      {agreements && (
        <label className="ov-auth__consent">
          <input
            type="checkbox"
            checked={accepted}
            disabled={blocked}
            onChange={(event) => setAccepted(event.target.checked)}
          />
          <span>
            {labels.agreementPrefix}
            <AgreementLinks agreements={agreements} />
          </span>
        </label>
      )}
      <Action action={secondaryAction} />
      {confirming && agreements && (
        <AgreementDialog
          agreements={agreements}
          labels={labels}
          onCancel={() => setConfirming(false)}
          onAgree={() => {
            setConfirming(false);
            submit(true);
          }}
        />
      )}
    </form>
  );
}
