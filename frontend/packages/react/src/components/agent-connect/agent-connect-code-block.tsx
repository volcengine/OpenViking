import { useExposure } from '../../shared/analytics/use-exposure.js';
import { Button } from '../../shared/primitives/button/button.js';
import { useId } from 'react';
import CopyIcon from '../../../../icons/svg/agent-connect/copy.svg?react';
import EyeIcon from '../../../../icons/svg/agent-connect/eye.svg?react';
import EyeInvisibleIcon from '../../../../icons/svg/agent-connect/eye-invisible.svg?react';
import CheckIcon from '../../../../icons/svg/scope-switcher/check.svg?react';
import { resolveAgentConnectLabels } from './agent-connect-copy.js';
import type { AgentConnectLocale } from './types.js';
import { useAgentConnectCopy } from './use-agent-connect-copy.js';

export interface CodeBlockControls {
  locale: AgentConnectLocale;
  visibleBlockId: string | null;
  onReveal: (id: string | null) => void;
  onCopy?: (text: string) => void;
}

interface CodeBlockProps extends CodeBlockControls {
  display: string;
  copyText: string;
  language?: string;
}

export function AgentConnectCodeBlock({
  display,
  copyText,
  language,
  locale,
  visibleBlockId,
  onReveal,
  onCopy,
}: CodeBlockProps) {
  const exposure = useExposure('agent_connect_code_block_copy_show');
  const id = useId();
  const revealed = visibleBlockId === id;
  const hasKey = display !== copyText;
  const labels = resolveAgentConnectLabels(locale);
  const { status, setRoot, copy } = useAgentConnectCopy(copyText, onCopy);
  const revealLabel = revealed ? labels.hideApiKey : labels.showApiKey;
  const copyLabel = status === 'copied' ? labels.copied : labels.copyCode;
  const RevealIcon = revealed ? EyeInvisibleIcon : EyeIcon;
  const ClipboardIcon = status === 'copied' ? CheckIcon : CopyIcon;
  const feedback =
    status === 'failed'
      ? labels.failed
      : status === 'copied'
        ? labels.copied
        : '';
  return (
    <div ref={setRoot} className="ov-agent-connect__code-block">
      <div className="ov-agent-connect__code-header">
        <span>{language || 'text'}</span>
        <div className="ov-agent-connect__code-actions">
          {hasKey ? (
            <Button
              size="mini"
              type="text"
              iconOnly
              htmlType="button"
              aria-pressed={revealed}
              aria-label={revealLabel}
              title={revealLabel}
              onClick={() => onReveal(revealed ? null : id)}
            >
              <RevealIcon aria-hidden="true" />
            </Button>
          ) : null}
          <Button
            ref={exposure}
            size="mini"
            type="text"
            iconOnly
            htmlType="button"
            disabled={status === 'pending'}
            aria-label={copyLabel}
            title={copyLabel}
            onClick={() => void copy()}
          >
            <ClipboardIcon aria-hidden="true" />
          </Button>
        </div>
      </div>
      <pre>
        <code>{revealed ? copyText : display}</code>
      </pre>
      <span
        className={
          status === 'failed'
            ? 'ov-agent-connect__feedback ov-agent-connect__feedback--error'
            : 'ov-agent-connect__code-feedback'
        }
        role="status"
        aria-live="polite"
      >
        {feedback}
      </span>
    </div>
  );
}
