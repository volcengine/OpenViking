import { useRef, useState } from 'react';

import CodexDetailIcon from '../../../../icons/svg/agent-connect/codex-detail.svg?react';
import CopyIcon from '../../../../icons/svg/agent-connect/copy.svg?react';
import type { AgentConnectEntry } from './agent-connect-design.js';
import { AGENT_CONNECT_COPY } from './agent-connect-copy.js';
import { getAgentConnectInstructions } from './agent-connect-instructions.js';
import { AgentConnectMarkdown } from './agent-connect-markdown.js';
import type { AgentConnectLocale } from './types.js';

interface DetailViewProps {
  selected: AgentConnectEntry;
  apiKey: string;
  locale: AgentConnectLocale;
  baseUrl: string;
  onCopy?: (text: string) => void;
}

/** 复制结果绑定当次正文；凭据变化后旧结果不会改变新详情反馈。 */
export function AgentConnectDetailView({
  selected,
  apiKey,
  locale,
  baseUrl,
  onCopy,
}: DetailViewProps) {
  const [guideId, setGuideId] = useState('');
  const activeGuide =
    selected.guides.find((guide) => guide.id === guideId) ?? selected.guides[0];
  const instructions = getAgentConnectInstructions(
    selected.id,
    locale,
    apiKey,
    baseUrl,
    activeGuide?.id,
  );
  const [result, setResult] = useState<{
    text: string;
    status: 'idle' | 'pending' | 'copied' | 'failed';
  }>({ text: '', status: 'idle' });
  const pending = useRef(false);
  const status = result.text === instructions.copy ? result.status : 'idle';
  const labels = AGENT_CONNECT_COPY[locale];
  const title = selected.name.endsWith(labels.integration)
    ? selected.name
    : `${selected.name} ${labels.integration}`;
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
    const text = instructions.copy;
    setResult({ text, status: 'pending' });
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      setResult({ text, status: 'failed' });
      return;
    } finally {
      pending.current = false;
    }
    setResult({ text, status: 'copied' });
    onCopy?.(text);
  }

  return (
    <section className="ov-agent-connect__detail" aria-label={title}>
      <div className="ov-agent-connect__identity">
        {selected.id === 'codex' ? (
          <CodexDetailIcon
            className="ov-agent-connect__detail-icon"
            aria-hidden="true"
          />
        ) : (
          <span
            className="ov-agent-connect__detail-icon ov-agent-connect__detail-icon--frame"
            aria-hidden="true"
          >
            <selected.Icon />
          </span>
        )}
        <div className="ov-agent-connect__summary">
          <h3 className="ov-agent-connect__detail-title">{title}</h3>
          <p className="ov-agent-connect__description">
            {selected.description}
          </p>
          {selected.tags.length > 0 ? (
            <div className="ov-agent-connect__tags">
              {selected.tags.map((tag) => (
                <span key={tag} className="ov-agent-connect__tag">
                  {tag}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      {selected.guides.length > 0 ? (
        <div
          className={`ov-agent-connect__guides${selected.id === 'cli' ? ' ov-agent-connect__guides--cli' : ''}`}
          role="group"
          aria-label={title}
        >
          {selected.guides.map((guide) => (
            <button
              key={guide.id}
              className="ov-agent-connect__guide"
              type="button"
              aria-pressed={guide.id === activeGuide?.id}
              onClick={() => setGuideId(guide.id)}
            >
              {guide.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className="ov-agent-connect__instructions">
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
          <span className="ov-agent-connect__agreement">
            {labels.agreement}
          </span>
        </p>
      </div>
    </section>
  );
}
