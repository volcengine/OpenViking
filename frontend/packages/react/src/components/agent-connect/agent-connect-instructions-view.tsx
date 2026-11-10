import { useCallback, useRef, useState } from 'react';

import CopyIcon from '../../../../icons/svg/agent-connect/copy.svg?react';
import { resolveAgentConnectLabels } from './agent-connect-copy.js';
import { AgentConnectMarkdown } from './agent-connect-markdown.js';
import type { AgentConnectLocale } from './types.js';

interface InstructionsViewProps {
  instructions: { fixed: boolean; display: string; copy: string };
  locale: AgentConnectLocale;
  agreementUrl?: string;
  onCopy?: (text: string) => void;
}

/** 由父视图的 key 隔离指南及凭据的复制生命周期。 */
export function AgentConnectInstructionsView({
  instructions,
  locale,
  agreementUrl,
  onCopy,
}: InstructionsViewProps) {
  const [status, setStatus] = useState<
    'idle' | 'pending' | 'copied' | 'failed'
  >('idle');
  const pending = useRef(false);
  const mounted = useRef(false);
  // ref 在提交阶段立即失效，避免权限结果早于被动 Effect 清理完成。
  const setRoot = useCallback((node: HTMLDivElement | null) => {
    mounted.current = node !== null;
  }, []);
  const labels = resolveAgentConnectLabels(locale);
  const message =
    status === 'copied'
      ? labels.copied
      : status === 'failed'
        ? labels.failed
        : '';
  const copyLabel = instructions.fixed ? labels.copy : labels.copyMarkdown;

  async function handleCopy() {
    if (pending.current) return;
    pending.current = true;
    setStatus('pending');
    try {
      await navigator.clipboard.writeText(instructions.copy);
    } catch {
      if (mounted.current) setStatus('failed');
      return;
    } finally {
      pending.current = false;
    }
    if (!mounted.current) return;
    setStatus('copied');
    onCopy?.(instructions.copy);
  }

  return (
    <div ref={setRoot} className="ov-agent-connect__instructions">
      <div className="ov-agent-connect__instructions-header">
        <h4 className="ov-agent-connect__instructions-title">
          {labels.instructions}
        </h4>
        <button
          className="ov-agent-connect__copy"
          type="button"
          disabled={status === 'pending'}
          onClick={() => void handleCopy()}
        >
          <CopyIcon aria-hidden="true" />
          {status === 'copied' ? labels.copied : copyLabel}
        </button>
      </div>
      <div className="ov-agent-connect__body">
        {instructions.fixed ? (
          instructions.display
        ) : (
          <AgentConnectMarkdown text={instructions.display} />
        )}
      </div>
      <span
        className={`ov-agent-connect__feedback${status === 'failed' ? ' ov-agent-connect__feedback--error' : ''}`}
        role="status"
        aria-live="polite"
      >
        {message}
      </span>
      <p className="ov-agent-connect__footer">
        {labels.notice}
        {agreementUrl ? (
          <>
            {labels.seeAgreement}
            <a
              className="ov-agent-connect__agreement"
              href={agreementUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              {labels.agreement}
            </a>
          </>
        ) : null}
      </p>
    </div>
  );
}
