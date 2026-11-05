import type { ModulePlaceholderProps } from './types.js';
import styles from './module-placeholder.module.css';

export function ModulePlaceholder({
  name,
  className,
  style,
  labels,
}: ModulePlaceholderProps & { name: string }) {
  return (
    <div
      className={[styles.root, className].filter(Boolean).join(' ')}
      style={style}
    >
      <p className={styles.label}>{labels?.title ?? name}</p>
      <p className={styles.description}>
        {labels?.description ??
          'Placeholder. Functionality is not available yet.'}
      </p>
    </div>
  );
}
