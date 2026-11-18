import { useExposure } from '../../shared/analytics/use-exposure.js';
import { useToast } from '../../shared/primitives/toast/use-toast.js';
import { useEffect, useRef, useState } from 'react';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';

/**
 * 路径旁的复制按钮。有 `source.path` 的文件在任何宿主下都需要它，因此内置在
 * 外壳里，而不是交给宿主渲染。
 *
 * The copy-path button beside the path. Any file with a `source.path` needs it
 * under every host, so it lives in the shell rather than being left to the host.
 */
export function FilePreviewCopyPath({
  label,
  disabled,
  successLabel,
  failureLabel,
  onClick,
}: {
  label: string;
  disabled: boolean;
  successLabel: string;
  failureLabel: string;
  onClick: () => Promise<boolean>;
}) {
  const exposure = useExposure('file_preview_copy_path_show');
  const [toast, toastHolder] = useToast();
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string }>();
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const lastMessage = useRef('');
  // Clear the browser feedback timer when its path button is unmounted.
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = async () => {
    clearTimeout(timer.current);
    let ok = false;
    try {
      ok = await onClick();
    } catch {
      /* Report clipboard failure below. */
    }
    if (!ok) {
      setFeedback(undefined);
      toast.error({ id: 'copy-path', content: failureLabel });
      return;
    }
    toast.clear();
    let message = successLabel;
    if (message === lastMessage.current) message += '\u00a0';
    lastMessage.current = message;
    setFeedback({ ok, message });
    timer.current = setTimeout(() => setFeedback(undefined), 2000);
  };
  return (
    <span className="ov-file-preview__copy-path">
      {toastHolder}
      <Tooltip content={label}>
        <button
          ref={exposure}
          type="button"
          className="ov-file-preview__action ov-file-preview__action--copy-path"
          aria-label={label}
          disabled={disabled}
          onClick={() => void copy()}
        >
          {/*
           * 两个方块对角叠放，墨迹上下左右对称，视觉重心落在正中。不要换成
           * 「大方块 + 小 L 形」那种画法：上半部分实心、下半部分只有细边，
           * 几何上居中但看起来偏上。
           *
           * Two squares offset diagonally. The ink is symmetric, so the visual
           * weight sits dead centre. Avoid the "big square + small L" variant: its
           * upper half is solid and its lower half a thin edge, which reads as
           * sitting high even though the geometry is centred.
           */}
          {feedback?.ok ? (
            <span aria-hidden="true">✓</span>
          ) : (
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M4.667 4.667V3.033c0-.653 0-.98.127-1.229a1.167 1.167 0 0 1 .51-.51c.25-.127.576-.127 1.23-.127h4.433c.653 0 .98 0 1.229.127.22.112.398.29.51.51.127.25.127.576.127 1.23v4.433c0 .653 0 .98-.127 1.229a1.167 1.167 0 0 1-.51.51c-.25.127-.576.127-1.23.127H9.333M3.033 12.833h4.434c.653 0 .98 0 1.229-.127.22-.112.398-.29.51-.51.127-.25.127-.576.127-1.229V6.533c0-.653 0-.98-.127-1.229a1.167 1.167 0 0 0-.51-.51c-.25-.127-.576-.127-1.23-.127H3.034c-.653 0-.98 0-1.23.127a1.167 1.167 0 0 0-.51.51c-.126.25-.126.576-.126 1.23v4.433c0 .653 0 .98.127 1.229.112.22.29.398.51.51.25.127.576.127 1.23.127Z" />
            </svg>
          )}
        </button>
      </Tooltip>
      <span className="ov-file-preview__copy-status" role="status">
        {feedback?.message}
      </span>
    </span>
  );
}
