import { useTrack } from '../../shared/analytics/use-track.js';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import { useLocale } from '../../shared/locale-context.js';
import { useId, useState } from 'react';

import { AgentConnectDetailView } from './agent-connect-detail.js';
import { getAgentConnectDesign } from './agent-connect-design.js';
import { resolveAgentConnectLabels } from './agent-connect-copy.js';
import { DEFAULT_BASE_URL } from './agent-connect-instructions.js';
import type {
  AgentConnectItem,
  AgentConnectProps,
  AgentConnectTabId,
} from './types.js';

export type {
  AgentConnectItem,
  AgentConnectProps,
  AgentConnectLocale,
  AgentConnectTabId,
} from './types.js';

/** 按设计稿固定文案与图标；选择与复制保持实例内交互。 */
export function AgentConnect({
  apiKey,
  locale: localeProp,
  baseUrl = DEFAULT_BASE_URL,
  agreementUrl,
  showOverseasTag,
  activeTabId: controlledTabId,
  selectedId: controlledSelectedId,
  onTabChange,
  onSelect,
  onCopy,
  className,
  ...rest
}: AgentConnectProps) {
  const track = useTrack();
  const exposure = useExposure('agent_connect_show');
  const locale = useLocale(localeProp);
  const id = useId();
  const [localTabId, setLocalTabId] = useState<AgentConnectTabId>('mainstream');
  const [localSelection, setLocalSelection] = useState({
    mainstream: 'codex',
    custom: 'mcp',
  });
  const activeTabId = controlledTabId ?? localTabId;
  const selectedId = controlledSelectedId ?? localSelection[activeTabId];
  const design = getAgentConnectDesign(locale, showOverseasTag);
  const labels = resolveAgentConnectLabels(locale);
  const tabs = design.tabs;
  const items = design[activeTabId];
  const activeIndex = Math.max(
    0,
    tabs.findIndex((tab) => tab.id === activeTabId),
  );
  // 两个内置分组均为非空固定目录。
  const selected = items.find((item) => item.id === selectedId) ?? items[0]!;

  function selectTab(tabId: AgentConnectTabId) {
    if (tabId !== activeTabId) track('agent_connect_tab_change');
    setLocalTabId(tabId);
    onTabChange?.(tabId);
  }

  function selectItem(item: AgentConnectItem) {
    track('agent_connect_select');
    setLocalSelection((previous) => ({ ...previous, [activeTabId]: item.id }));
    onSelect?.({ id: item.id, name: item.name });
  }

  return (
    <div
      {...rest}
      ref={exposure}
      className={['ov-agent-connect', className].filter(Boolean).join(' ')}
    >
      <div className="ov-agent-connect__canvas">
        <header className="ov-agent-connect__header">
          <h2 className="ov-agent-connect__title">{labels.title}</h2>
          <div
            className="ov-agent-connect__tabs"
            role="tablist"
            aria-label={labels.title}
          >
            {tabs.map((tab, index) => (
              <button
                key={tab.id}
                id={`${id}-tab-${index}`}
                className="ov-agent-connect__tab"
                type="button"
                role="tab"
                aria-selected={index === activeIndex}
                aria-controls={`${id}-panel`}
                tabIndex={index === activeIndex ? 0 : -1}
                onClick={() => selectTab(tab.id)}
                onKeyDown={(event) => {
                  let next: number;
                  if (event.key === 'Home') next = 0;
                  else if (event.key === 'End') next = tabs.length - 1;
                  else if (
                    event.key === 'ArrowRight' ||
                    event.key === 'ArrowLeft'
                  )
                    next =
                      (index +
                        (event.key === 'ArrowRight' ? 1 : -1) +
                        tabs.length) %
                      tabs.length;
                  else return;
                  const nextTab = tabs[next];
                  if (!nextTab) return;
                  event.preventDefault();
                  const buttons =
                    event.currentTarget.parentElement?.querySelectorAll(
                      'button',
                    );
                  buttons?.[next]?.focus();
                  selectTab(nextTab.id);
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </header>
        <div
          id={`${id}-panel`}
          className="ov-agent-connect__layout"
          role="tabpanel"
          aria-labelledby={`${id}-tab-${activeIndex}`}
        >
          <ul className="ov-agent-connect__list">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="ov-agent-connect__item"
                  aria-pressed={item.id === selected.id}
                  onClick={() => selectItem(item)}
                >
                  <item.Icon
                    className="ov-agent-connect__item-icon"
                    aria-hidden="true"
                  />
                  <span className="ov-agent-connect__item-name">
                    {item.name}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <AgentConnectDetailView
            key={`${activeTabId}:${selected.id}`}
            selected={selected}
            apiKey={apiKey}
            locale={locale}
            baseUrl={baseUrl}
            agreementUrl={agreementUrl}
            onCopy={onCopy}
          />
        </div>
      </div>
    </div>
  );
}
