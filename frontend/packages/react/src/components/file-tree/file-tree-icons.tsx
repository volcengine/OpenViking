import styles from './file-tree.module.css';

export function Chevron({ expanded }: { expanded: boolean }) {
  return (
    <svg
      aria-hidden="true"
      className={styles.chevron}
      data-expanded={expanded || undefined}
      viewBox="0 0 16 16"
      width="12"
      height="12"
    >
      <path
        d="M6 3.5 10.5 8 6 12.5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

export function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      className={styles.icon}
      viewBox="0 0 16 16"
      width="14"
      height="14"
    >
      <path
        d={
          open
            ? 'M1.5 13V4.5A1 1 0 0 1 2.5 3.5h3l1.2 1.5h5.8a1 1 0 0 1 1 1V7M1.5 13l1.6-5h11.4l-1.6 5H1.5Z'
            : 'M1.5 12.5v-9a1 1 0 0 1 1-1h3l1.2 1.5h6.8a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1Z'
        }
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.2"
      />
    </svg>
  );
}

export function FileIcon() {
  return (
    <svg
      aria-hidden="true"
      className={styles.icon}
      viewBox="0 0 16 16"
      width="14"
      height="14"
    >
      <path
        d="M4 1.5h5L12.5 5v9.5h-8.5V1.5Z"
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.2"
      />
      <path
        d="M9 1.5V5h3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
  );
}

export function Spinner() {
  return <span aria-hidden="true" className={styles.spinner} />;
}
