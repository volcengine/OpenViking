import { useTrack } from '../../../shared/analytics/use-track.js';
import { useExposure } from '../../../shared/analytics/use-exposure.js';
import { useAuthLabels } from '../../../shared/auth/use-auth-labels.js';
import { Action } from '../../../shared/auth/fields.js';
import type { InvitationConfirmationProps } from './types.js';
export function InvitationConfirmation({
  details,
  loading,
  disabled,
  error,
  decline,
  defer,
  className,
  onAccept,
  title,
  description,
  locale,
  acceptLabel,
}: InvitationConfirmationProps) {
  const track = useTrack();
  const exposure = useExposure('invitation_confirmation_show');
  const labels = useAuthLabels(locale);
  return (
    <section
      ref={exposure}
      className={`ov-auth ${className ?? ''}`}
      aria-busy={loading || undefined}
    >
      {title && <h2>{title}</h2>}
      {description && <p className="ov-auth__description">{description}</p>}
      <dl className="ov-auth__details">
        {details.map((item, index) => (
          <div key={index}>
            <dt>{item.label}</dt>
            <dd>{item.value}</dd>
          </div>
        ))}
      </dl>
      {error && (
        <div className="ov-auth__error" role="alert">
          {error}
        </div>
      )}
      <fieldset disabled={loading || disabled}>
        <button
          type="button"
          className="ov-auth__primary ov-auth__submit"
          onClick={() => {
            track('invitation_confirmation_accept_click');
            onAccept?.();
          }}
        >
          {acceptLabel ?? labels.acceptInvitation}
          {loading && ' …'}
        </button>
        <Action action={decline} />
        <Action action={defer} />
      </fieldset>
    </section>
  );
}
