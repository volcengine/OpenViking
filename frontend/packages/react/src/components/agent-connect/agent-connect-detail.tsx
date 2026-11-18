import { useState } from 'react';

import CodexDetailIcon from '../../../../icons/svg/agent-connect/codex-detail.svg?react';
import type { AgentConnectEntry } from './agent-connect-design.js';
import { resolveAgentConnectLabels } from './agent-connect-copy.js';
import { getAgentConnectInstructions } from './agent-connect-instructions.js';
import { AgentConnectInstructionsView } from './agent-connect-instructions-view.js';
import type { AgentConnectLocale } from './types.js';

interface DetailViewProps {
  selected: AgentConnectEntry;
  apiKey: string;
  locale: AgentConnectLocale;
  baseUrl: string;
  agreementUrl?: string;
  onCopy?: (text: string) => void;
}

/** 指南和凭据变化时重建复制区；切换语言保留曝光周期，凭据不进入 DOM 或 React key。 */
export function AgentConnectDetailView({
  selected,
  apiKey,
  locale,
  baseUrl,
  agreementUrl,
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
  const [credentials, setCredentials] = useState({
    apiKey,
    baseUrl,
    revision: 0,
  });
  // 有界的渲染期调整：输入变更仅重建复制区，保留当前指南选择。
  if (credentials.apiKey !== apiKey || credentials.baseUrl !== baseUrl) {
    setCredentials({ apiKey, baseUrl, revision: credentials.revision + 1 });
  }
  const labels = resolveAgentConnectLabels(locale);
  const title = selected.name.endsWith(labels.integration)
    ? selected.name
    : `${selected.name} ${labels.integration}`;

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
      <AgentConnectInstructionsView
        key={`${selected.id}:${activeGuide?.id ?? ''}:${credentials.revision}`}
        instructions={instructions}
        apiKey={apiKey}
        locale={locale}
        agreementUrl={agreementUrl}
        onCopy={onCopy}
      />
    </section>
  );
}
