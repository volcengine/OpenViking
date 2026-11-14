import { withMonitoring } from '../../../shared/monitoring/with-monitoring.js';
import { useTrack } from '../../../shared/analytics/use-track.js';
import type { LoginMethodSelectorProps } from './types.js';
function LoginMethodSelectorView({
  methods,
  disabled,
  className,
  onSelect,
  title,
  description,
}: LoginMethodSelectorProps) {
  const track = useTrack();
  return (
    <div className={`ov-auth ${className ?? ''}`}>
      {title && <h2>{title}</h2>}
      {description && <p className="ov-auth__description">{description}</p>}
      <div className="ov-auth__methods">
        {methods.map((method) => (
          <button
            key={method.id}
            type="button"
            disabled={disabled || method.disabled}
            onClick={() => {
              track('login_method_selector_select');
              onSelect?.(method.id);
            }}
          >
            {method.icon}
            {method.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export const LoginMethodSelector = withMonitoring(
  LoginMethodSelectorView,
  'LoginMethodSelector',
);
