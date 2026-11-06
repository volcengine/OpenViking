import type { ModulePlaceholderProps } from './types.js';

export function ModulePlaceholder({
  name,
  className,
  style,
  labels,
}: ModulePlaceholderProps & { name: string }) {
  return (
    <div
      className={['ov-module-placeholder', className].filter(Boolean).join(' ')}
      style={style}
    >
      <p className="ov-module-placeholder__label">{labels?.title ?? name}</p>
      <p className="ov-module-placeholder__description">
        {labels?.description ??
          'Placeholder. Functionality is not available yet.'}
      </p>
    </div>
  );
}
