export interface ConsoleSkeletonProps {
  'aria-label': string;
}

/** Occupies the Console panel while its host resolves the available modes. */
export function ConsoleSkeleton({ 'aria-label': label }: ConsoleSkeletonProps) {
  return (
    <div className="ov-console-skeleton" role="status" aria-label={label}>
      <div className="ov-console-skeleton__layout" aria-hidden="true">
        <div className="ov-console-skeleton__body">
          <div className="ov-console-skeleton__heading" />
          <div className="ov-console-skeleton__subtitle" />
          <div className="ov-console-skeleton__actions">
            {[0, 1, 2].map((row) => (
              <div className="ov-console-skeleton__action" key={row}>
                <div className="ov-console-skeleton__action-icon" />
                <div className="ov-console-skeleton__action-label" />
                <div className="ov-console-skeleton__action-detail" />
              </div>
            ))}
          </div>
        </div>
        <div className="ov-console-skeleton__composer">
          <div className="ov-console-skeleton__prompt" />
          <div className="ov-console-skeleton__send" />
        </div>
      </div>
    </div>
  );
}
