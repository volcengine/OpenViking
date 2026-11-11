import { Button } from '../../shared/components/button/button.js';
import { useEffect, useRef, useState } from 'react';

import CopySvg from '../../../../icons/svg/tui/copy.svg?react';
import CheckSvg from '../../../../icons/svg/scope-switcher/check.svg?react';
import { Tooltip } from '../../shared/components/tooltip/index.js';
import { copyTuiText } from './tui-text.js';

export interface TuiCopyButtonProps {
  text: string;
  label: string;
  copiedLabel: string;
  onCopy?: (text: string) => void;
  className?: string;
}

/** 纯图标复制按钮：Tooltip 展示动作与复制成功反馈。 */
export function TuiCopyButton({
  text,
  label,
  copiedLabel,
  onCopy,
  className,
}: TuiCopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<number | undefined>(undefined);

  // Clear this button's browser feedback timer on unmount; it cannot be cleaned
  // up by a later click when the result has been removed from the document.
  useEffect(() => () => window.clearTimeout(resetTimer.current), []);

  const handleCopy = () => {
    void copyTuiText(text).then((succeeded) => {
      if (!succeeded) return;
      window.clearTimeout(resetTimer.current);
      setCopied(true);
      onCopy?.(text);
      resetTimer.current = window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <Tooltip content={copied ? copiedLabel : label}>
      <Button
        size="mini"
        type="text"
        iconOnly
        htmlType="button"
        className={['ov-tui__copy', className].filter(Boolean).join(' ')}
        aria-label={copied ? copiedLabel : label}
        onClick={handleCopy}
      >
        {copied ? (
          <CheckSvg aria-hidden="true" focusable="false" />
        ) : (
          <CopySvg aria-hidden="true" focusable="false" />
        )}
      </Button>
    </Tooltip>
  );
}
