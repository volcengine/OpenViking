import { useCallback, useRef, useState } from 'react';
import { CheckIcon, CopyIcon } from '../../icons/index.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
import type { ExperienceDashboardLabels } from './types.js';

/**
 * 复制按钮：图标形态只出图标、悬停提示，文字形态带标签。复制成功后两秒回到原状。
 *
 * 与看板其他图标按钮共用 `.ov-experience-dashboard__icon-button` 的尺寸与悬停
 * 规则，因此这里的样式只区分「有标签」与「无标签」。
 *
 * A copy button: the icon form shows only an icon with a hover hint, the labelled
 * form carries text. It returns to its idle state two seconds after a successful
 * copy.
 *
 * It shares the icon-button size and hover rules with the board's other icon
 * buttons, so the styles here only separate the labelled and unlabelled forms.
 */
export function ExperienceCopyAction({
  value,
  labels: t,
  labelled = false,
  disabled = false,
}: {
  value?: string;
  labels: ExperienceDashboardLabels;
  labelled?: boolean;
  disabled?: boolean;
}) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timer = useRef<number | undefined>(undefined);
  const copy = useCallback(async () => {
    if (!value || disabled) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      setState('failed');
      return;
    }
    setState('copied');
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState('idle'), 2000);
  }, [disabled, value]);
  const feedback =
    state === 'copied' ? t.copied : state === 'failed' ? t.copyFailed : t.copy;
  const button = (
    <button
      type="button"
      className={[
        'ov-experience-dashboard__copy',
        labelled
          ? 'ov-experience-dashboard__copy--labelled'
          : 'ov-experience-dashboard__icon-button',
      ].join(' ')}
      aria-label={feedback}
      disabled={disabled || !value}
      onClick={() => void copy()}
    >
      {state === 'copied' ? (
        <CheckIcon width={12} height={12} />
      ) : (
        <CopyIcon width={12} height={12} />
      )}
      {labelled ? <span>{feedback}</span> : null}
    </button>
  );
  return labelled ? (
    button
  ) : (
    <Tooltip content={feedback} placement="top" disabled={state === 'copied'}>
      {button}
    </Tooltip>
  );
}
