import { useRef, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { createTuiDemoRecord } from '#tui-demo-result';
import { ArrowRightIcon } from '@openviking/react/icons';
import { Tui } from '@openviking/react/components/tui';
import type {
  TuiCommandDefinition,
  TuiRecord,
} from '@openviking/react/components/tui';
import '@openviking/react/components/tui/style.css';
import { createTuiCommands, createTuiCommandGroups } from '#tui-commands';

// The empty state belongs to the host. These styles travel with the copyable demo.
const emptyStyles = `
.ov-demo-tui-empty {
  --ov-ui-demo-tui-muted: var(--ov-ui-tui-muted);
  inline-size: 100%;
  min-inline-size: 0;
  align-self: stretch;
  padding: 32px 4px;
  color: var(--ov-ui-color-text-2, var(--ov-ui-text-2-default));
  text-align: start;
}
.ov-demo-tui-empty .ov-demo-tui-empty__title {
  display: flex;
  align-items: center;
  margin: 0;
  font-family: var(--ov-ui-font-family, 'Trebuchet MS', sans-serif);
  font-size: clamp(1.5rem, 1.25rem + 2.5cqi, 2.25rem);
  font-weight: 400;
  line-height: 1.2;
  letter-spacing: 0.035em;
}
.ov-demo-tui-empty__title::after {
  content: '';
  inline-size: 0.32em;
  block-size: 0.9em;
  margin-inline-start: 0.15em;
  background: currentColor;
  opacity: 0.16;
}
.ov-demo-tui-empty .ov-demo-tui-empty__subtitle {
  margin: 16px 0 40px;
  color: var(--ov-ui-demo-tui-muted);
  font-size: 15px;
}
.ov-demo-tui-empty .ov-demo-tui-empty__label {
  margin: 0 0 12px;
  color: var(--ov-ui-demo-tui-muted);
}
.ov-demo-tui-empty .ov-demo-tui-empty__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.ov-demo-tui-empty .ov-demo-tui-empty__item { margin: 0; }
.ov-demo-tui-empty__action {
  display: grid;
  grid-template-columns: 14px minmax(132px, 0.8fr) minmax(0, 1.2fr);
  align-items: center;
  gap: 12px;
  inline-size: 100%;
  min-block-size: 52px;
  border: 0;
  border-radius: 16px;
  padding: 12px 16px;
  background: var(--ov-ui-tui-card-bg);
  color: inherit;
  font: inherit;
  text-align: start;
  cursor: pointer;
}
.ov-demo-tui-empty__action:hover { background: var(--ov-ui-tui-bubble-bg); }
.ov-demo-tui-empty__action > svg { translate: 0; }
@keyframes ov-demo-tui-empty-arrow-nudge {
  0%, 100% { translate: 0; }
  30% { translate: -2px 0; }
  65% { translate: 3px 0; }
}
@media (hover: hover) and (prefers-reduced-motion: no-preference) {
  .ov-demo-tui-empty__action:hover > svg {
    animation: ov-demo-tui-empty-arrow-nudge 360ms ease-in-out;
  }
}
.ov-demo-tui-empty__action:focus-visible {
  outline: 2px solid var(--ov-ui-tui-link);
  outline-offset: 3px;
}
.ov-demo-tui-empty__name {
  color: var(--ov-ui-color-text-1, var(--ov-ui-text-1-default));
  font-family: var(--ov-ui-font-mono, ui-monospace, monospace);
  font-size: 15px;
  overflow-wrap: anywhere;
}
.ov-demo-tui-empty__description {
  color: var(--ov-ui-color-text-description, var(--ov-ui-text-description-default));
  overflow-wrap: anywhere;
}
@container (max-width: 420px) {
  .ov-demo-tui-empty { padding-block: 16px; }
  .ov-demo-tui-empty__action { grid-template-columns: 14px minmax(0, 1fr); gap: 4px 12px; }
  .ov-demo-tui-empty__description { grid-column: 2; }
}
`;

function EmptyTuiExample({
  locale,
  commands,
  quickCommands,
}: {
  locale: 'en-US' | 'zh-CN';
  commands: readonly TuiCommandDefinition[];
  quickCommands: readonly TuiCommandDefinition[];
}) {
  const isChinese = locale === 'zh-CN';
  const [records, setRecords] = useState<readonly TuiRecord[]>([]);
  const [value, setValue] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  return (
    <div
      ref={containerRef}
      style={{ display: 'flex', flexDirection: 'column', height: 720 }}
    >
      <style>{emptyStyles}</style>
      <Tui
        records={records}
        value={value}
        locale={locale}
        commands={commands}
        commandGroups={createTuiCommandGroups(locale)}
        onValueChange={setValue}
        emptyState={
          <div className="ov-demo-tui-empty">
            <h3 className="ov-demo-tui-empty__title">OpenViking CLI</h3>
            <p className="ov-demo-tui-empty__subtitle">
              {isChinese
                ? 'CLI 直接操作上下文'
                : 'Work with your context through the CLI'}
            </p>
            <p className="ov-demo-tui-empty__label">
              {isChinese ? '快速体验：' : 'Quick start:'}
            </p>
            <ul className="ov-demo-tui-empty__list">
              {quickCommands.map((command) => (
                <li key={command.name} className="ov-demo-tui-empty__item">
                  <button
                    type="button"
                    className="ov-demo-tui-empty__action"
                    onClick={() => {
                      setValue(`${command.inputText ?? command.name} `);
                      containerRef.current
                        ?.querySelector('textarea')
                        ?.focus({ preventScroll: true });
                    }}
                  >
                    <ArrowRightIcon width={14} height={14} focusable="false" />
                    <span className="ov-demo-tui-empty__name">
                      {command.name}
                    </span>
                    <span className="ov-demo-tui-empty__description">
                      {command.summary ?? command.description}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        }
        onSubmit={(command) => {
          const createdAtMs = Date.now();
          setRecords((current) => [
            ...current,
            {
              ...createTuiDemoRecord(command, locale),
              id: `empty-demo-${current.length}`,
              createdAtMs,
            },
          ]);
          setValue('');
        }}
      />
    </div>
  );
}

export default function TuiEmpty() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const commands = createTuiCommands(locale);
  const quickCommands = [
    'add-resource',
    'add-skill',
    'add-memory',
    'find',
    'compile',
  ].flatMap((name) => commands.filter((command) => command.name === name));
  return (
    <EmptyTuiExample
      key={locale}
      locale={locale}
      commands={commands}
      quickCommands={quickCommands}
    />
  );
}
