import { useState } from 'react';

import CopyIcon from '../../../../icons/svg/agent-connect/copy.svg?react';
import { resolveAgentConnectLabels } from './agent-connect-copy.js';
import { AgentConnectMarkdown } from './agent-connect-markdown.js';
import type { AgentConnectLocale } from './types.js';
import type { getAgentConnectInstructions } from './agent-connect-instructions.js';
import { AgentConnectCodeBlock } from './agent-connect-code-block.js';
import { useAgentConnectCopy } from './use-agent-connect-copy.js';

interface InstructionsViewProps {
  instructions: ReturnType<typeof getAgentConnectInstructions>;
  apiKey: string;
  locale: AgentConnectLocale;
  agreementUrl?: string;
  onCopy?: (text: string) => void;
}

/** 由父视图的 key 隔离指南及凭据的复制生命周期。 */
export function AgentConnectInstructionsView({
  instructions,
  apiKey,
  locale,
  agreementUrl,
  onCopy,
}: InstructionsViewProps) {
  const [visibleBlockId, onReveal] = useState<string | null>(null);
  const { status, setRoot, copy } = useAgentConnectCopy(
    instructions.copy,
    onCopy,
  );
  const controls = { locale, visibleBlockId, onReveal, onCopy };
  const command = instructions.command;
  const commandOffset = command
    ? instructions.display.indexOf(command.display)
    : -1;
  const labels = resolveAgentConnectLabels(locale);
  const message =
    status === 'copied'
      ? labels.copied
      : status === 'failed'
        ? labels.failed
        : '';
  const copyLabel = instructions.fixed ? labels.copy : labels.copyMarkdown;

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
          onClick={() => void copy()}
        >
          <CopyIcon aria-hidden="true" />
          {status === 'copied' ? labels.copied : copyLabel}
        </button>
      </div>
      <div className="ov-agent-connect__body">
        {command ? (
          <>
            {instructions.display.slice(0, commandOffset)}
            <AgentConnectCodeBlock
              {...controls}
              language="bash"
              display={command.display}
              copyText={command.copy}
            />
            {instructions.display.slice(commandOffset + command.display.length)}
          </>
        ) : (
          <AgentConnectMarkdown
            {...controls}
            text={instructions.markdown}
            keyToken={instructions.keyToken}
            apiKey={apiKey}
          />
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
