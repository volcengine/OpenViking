import { Button } from '../../shared/components/button/button.js';
/**
 * 请求失败时的占位：一句结论、一行原始原因、一个重试按钮。
 *
 * 结论用可替换文案，原因原样显示服务端或传输层给的消息——把「403 Forbidden」
 * 换成「加载失败」会让人分不清是权限、网络还是数据确实不存在，而这三者的处置
 * 完全不同。
 *
 * The placeholder shown when a request fails: a conclusion, the raw reason and a
 * retry button.
 *
 * The conclusion is replaceable copy while the reason is shown as the server or the
 * transport reported it — replacing "403 Forbidden" with "Failed to load" hides
 * whether this is a permission problem, a network problem or genuinely missing
 * data, and the three call for different actions.
 */
export interface FileBrowserFailureProps {
  message: string;
  detail?: string;
  retryLabel: string;
  onRetry: () => void;
}

export function FileBrowserFailure({
  message,
  detail,
  retryLabel,
  onRetry,
}: FileBrowserFailureProps) {
  return (
    <div className="ov-file-browser__failure" role="alert">
      <p className="ov-file-browser__failure-title">{message}</p>
      {detail ? (
        <p className="ov-file-browser__failure-detail">{detail}</p>
      ) : null}
      <Button
        size="small"
        type="default"
        htmlType="button"
        className="ov-file-browser__failure-retry"
        onClick={onRetry}
      >
        {retryLabel}
      </Button>
    </div>
  );
}
