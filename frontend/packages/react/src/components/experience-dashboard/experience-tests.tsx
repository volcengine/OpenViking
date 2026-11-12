import { Button } from '../../shared/primitives/button/button.js';
import { useState } from 'react';
import type {
  ExperienceDashboardData,
  ExperienceDashboardLabels,
} from './types.js';

function CopyButton({
  value,
  labels: t,
}: {
  value: string;
  labels: ExperienceDashboardLabels;
}) {
  const [result, setResult] = useState<{
    value: string;
    state: 'pending' | 'copied' | 'failed';
  }>();
  const state = result?.value === value ? result.state : undefined;
  const copy = async () => {
    setResult({ value, state: 'pending' });
    try {
      await navigator.clipboard.writeText(value);
      setResult({ value, state: 'copied' });
    } catch {
      setResult({ value, state: 'failed' });
    }
  };
  return (
    <Button
      size="small"
      type="default"
      htmlType="button"
      disabled={!value || state === 'pending'}
      aria-live="polite"
      onClick={() => void copy()}
    >
      {state === 'copied'
        ? t.copied
        : state === 'failed'
          ? t.copyFailed
          : t.copy}
    </Button>
  );
}

export function ExperienceTests({
  items,
  labels: t,
  onOpenUri,
}: {
  items: NonNullable<ExperienceDashboardData['testMaterials']>;
  labels: ExperienceDashboardLabels;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <div className="ov-experience-dashboard__tests">
      {items.map((item) => (
        <article key={item.id}>
          <header>
            <div className="ov-experience-dashboard__test-heading">
              <strong>{item.title}</strong>
              <div className="ov-experience-dashboard__test-meta">
                <span>{t.sourceUri}</span>
                <code title={item.uri}>{item.uri ?? '—'}</code>
                {item.uri ? (
                  <>
                    <CopyButton value={item.uri} labels={t} />
                    <Button
                      size="small"
                      type="default"
                      htmlType="button"
                      disabled={!onOpenUri}
                      onClick={() => item.uri && onOpenUri?.(item.uri)}
                    >
                      {t.open}
                    </Button>
                  </>
                ) : null}
              </div>
              {item.taskQuery ? (
                <div className="ov-experience-dashboard__test-meta">
                  <span>{t.task}</span>
                  <p>{item.taskQuery}</p>
                </div>
              ) : null}
            </div>
            <CopyButton value={item.content} labels={t} />
          </header>
          <div className="ov-experience-dashboard__test-content">
            {item.content.split('\n').map((line, index) => {
              const role = line.match(/^(User|Assistant):(.*)$/);
              return (
                <div key={index}>
                  {role ? (
                    <>
                      <strong>{role[1]}:</strong>
                      {role[2]}
                    </>
                  ) : line.startsWith('# ') ? (
                    <strong>{line}</strong>
                  ) : (
                    line || '\u00a0'
                  )}
                </div>
              );
            })}
          </div>
        </article>
      ))}
    </div>
  );
}
