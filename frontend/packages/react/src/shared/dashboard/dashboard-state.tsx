export interface DashboardStatus {
  loading?: boolean;
  error?: Error | null;
  onRetry?: () => void;
}

export function DashboardState({
  state = {},
  empty,
  loading,
  failed,
  retry,
}: {
  state?: DashboardStatus;
  empty: string;
  loading: string;
  failed: string;
  retry: string;
}) {
  return (
    <div className="ov-dashboard-state" aria-busy={state.loading}>
      <p aria-live="polite">
        {state.loading ? loading : state.error ? failed : empty}
      </p>
      {state.error ? (
        <>
          <p>{state.error.message}</p>
          <button
            type="button"
            disabled={!state.onRetry}
            onClick={state.onRetry}
          >
            {retry}
          </button>
        </>
      ) : null}
    </div>
  );
}
