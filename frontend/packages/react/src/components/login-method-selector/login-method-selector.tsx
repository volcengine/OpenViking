import type { LoginMethodSelectorProps } from './types.js';
export function LoginMethodSelector({
  methods,
  disabled,
  className,
  onSelect,
  title,
  description,
}: LoginMethodSelectorProps) {
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
            onClick={() => onSelect?.(method.id)}
          >
            {method.icon}
            {method.label}
          </button>
        ))}
      </div>
    </div>
  );
}
