import { useState } from 'react';
import { ScanSearchIcon } from '../../icons/index.js';
import { Button } from '../../shared/primitives/button/button.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import type { VikingBotLabels } from './model.js';

export function SessionMeta({
  id,
  uri,
  labels,
  onCopy,
  onOpenUri,
}: {
  id: string;
  uri?: string;
  labels: Required<VikingBotLabels>;
  onCopy?: (text: string) => void;
  onOpenUri?: (uri: string) => void;
}) {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const copyLabel = copiedId === id ? labels.copied : labels.copy;
  const copy = async () => {
    if (onCopy) {
      onCopy(id);
      return;
    }
    try {
      await navigator.clipboard.writeText(id);
      setCopiedId(id);
    } catch {
      setCopiedId(null);
    }
  };
  const compact = id.length > 22 ? id.slice(0, 10) + '...' + id.slice(-6) : id;
  return (
    <div className="ov-viking-bot__session-meta">
      <span>{labels.session}</span>
      <code title={id}>{compact}</code>
      <Tooltip content={copyLabel}>
        <Button
          size="mini"
          type="text"
          iconOnly
          htmlType="button"
          aria-label={copyLabel}
          onClick={() => void copy()}
        >
          <svg
            viewBox="0 0 16 16"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            aria-hidden="true"
          >
            <rect x="2.5" y="5" width="7.2" height="8.5" rx="1.2" />
            <path d="M5 2.5h7.2a1.2 1.2 0 0 1 1.2 1.2V11" />
          </svg>
        </Button>
      </Tooltip>
      {uri && onOpenUri ? (
        <Tooltip content={labels.locateSession}>
          <Button
            size="mini"
            type="text"
            iconOnly
            htmlType="button"
            aria-label={labels.locateSession}
            onClick={() => onOpenUri(uri)}
          >
            <ScanSearchIcon width={14} height={14} />
          </Button>
        </Tooltip>
      ) : null}
    </div>
  );
}
