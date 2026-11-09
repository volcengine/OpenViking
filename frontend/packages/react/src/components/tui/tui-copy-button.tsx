import { useState } from 'react';

import { copyTuiText } from './tui-text.js';

export interface TuiCopyButtonProps {
  text: string;
  label: string;
  copiedLabel: string;
  onCopy?: (text: string) => void;
  className?: string;
}

/** 复制按钮：成功后短暂显示已复制，并通知宿主。 */
export function TuiCopyButton({
  text,
  label,
  copiedLabel,
  onCopy,
  className,
}: TuiCopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    void copyTuiText(text).then((succeeded) => {
      if (!succeeded) return;
      setCopied(true);
      onCopy?.(text);
      window.setTimeout(() => setCopied(false), 1600);
    });
  };

  return (
    <button
      type="button"
      className={['ov-tui__copy', className].filter(Boolean).join(' ')}
      onClick={handleCopy}
    >
      {copied ? copiedLabel : label}
    </button>
  );
}
