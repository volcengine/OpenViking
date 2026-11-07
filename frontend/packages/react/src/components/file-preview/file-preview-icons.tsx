export function FileIcon() {
  return (
    <svg
      aria-hidden="true"
      className="ov-file-preview__icon"
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

export function CopyIcon() {
  return (
    <svg
      aria-hidden="true"
      className="ov-file-preview__action-icon"
      viewBox="0 0 16 16"
      width="12"
      height="12"
    >
      <rect
        x="5.5"
        y="5.5"
        width="8"
        height="9"
        rx="1.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path
        d="M10.5 5.5v-2a1 1 0 0 0-1-1h-7a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
  );
}
