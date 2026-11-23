import { Button } from '../primitives/button/button.js';
import { Empty } from '../primitives/empty/empty.js';

export interface DashboardStatus {
  loading?: boolean;
  error?: Error | null;
  onRetry?: () => void;
}

/**
 * 一个区域的状态：加载中、空、或失败加重试。
 *
 * 重试用次要按钮：它是这个区域里唯一可点的东西，纯文字形态在成片留白里看不出是
 * 可操作的控件。
 *
 * The state of one section: loading, empty, or failed with a retry.
 *
 * The retry is a secondary button: it is the only thing to click in the section, and a
 * text-only form reads as plain copy in that much whitespace. The empty case uses the
 * shared compact illustration.
 */
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
  if (!state.loading && !state.error) {
    return (
      <div className="ov-dashboard-state">
        <Empty size="compact" illustration="chart" title={empty} />
      </div>
    );
  }
  return (
    <div className="ov-dashboard-state" aria-busy={state.loading}>
      <p aria-live="polite">{state.loading ? loading : failed}</p>
      {state.error ? (
        <>
          <p>{state.error.message}</p>
          <Button
            type="secondary"
            size="small"
            disabled={!state.onRetry}
            onClick={state.onRetry}
          >
            {retry}
          </Button>
        </>
      ) : null}
    </div>
  );
}
