/** The built-in decorative loading ring shared by internal controls. */
export function LoadingIcon({ className }: { className?: string }) {
  return (
    <span
      className={['ov-loading-icon', className].filter(Boolean).join(' ')}
      aria-hidden="true"
    />
  );
}
