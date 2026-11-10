/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- Labelled conflict region must support keyboard horizontal scrolling. */
import { useState } from 'react';
import { useLocale } from '../../shared/locale-context.js';
import { WikiWarningIcon } from '../../icons/index.js';
import { DashboardState } from '../../shared/dashboard/dashboard-state.js';
import { wikiLabels } from './labels.js';
import { WikiConflictCard } from './wiki-conflict-card.js';
import { WikiFileIcon } from './wiki-file-icon.js';
import { WikiSeverityFilter } from './wiki-severity-filter.js';
import type { WikiDashboardProps, WikiConflict } from './types.js';

export function WikiDashboard(props: WikiDashboardProps) {
  const {
    kind,
    data,
    editDisabledReason,
    discussDisabledReason,
    locale: localeProp,
    onOpenUri,
    onOpenDashboard,
    onEdit,
    onDiscuss,
    labels,
    loading,
    error,
    onRetry,
    className,
    ...rest
  } = props;
  const locale = useLocale(localeProp);
  const t = wikiLabels(locale, labels);
  const [severity, setSeverity] = useState<WikiConflict['severity'] | 'all'>(
    'all',
  );
  const conflicts =
    kind === 'file'
      ? (data?.conflicts.filter(
          (item) => severity === 'all' || item.severity === severity,
        ) ?? [])
      : [];
  const count = data
    ? kind === 'file'
      ? data.conflicts.filter((item) => item.state === 'unfixed').length
      : data.conflictFileCount
    : undefined;
  return (
    <div
      {...rest}
      className={['ov-wiki-dashboard', className].filter(Boolean).join(' ')}
    >
      <header className="ov-wiki-dashboard__header">
        <h2>{t.title}</h2>
      </header>
      <div className="ov-wiki-dashboard__body">
        <dl className="ov-wiki-dashboard__statistics">
          <div>
            <WikiWarningIcon width={14} height={14} />
            <dd>
              {count === undefined
                ? '—'
                : kind === 'directory' && count >= 100
                  ? '99+'
                  : `${count}${kind === 'directory' && data?.limited ? '+' : ''}`}
            </dd>
            <dt>{kind === 'file' ? t.pending : t.conflictFiles}</dt>
          </div>
        </dl>
        <div className="ov-wiki-dashboard__toolbar">
          <h3>{t.conflicts}</h3>
          {kind === 'file' ? (
            <WikiSeverityFilter
              value={severity}
              onChange={setSeverity}
              labels={t}
            />
          ) : null}
        </div>
        {kind === 'file' && conflicts.length ? (
          <div
            className="ov-wiki-dashboard__conflicts"
            role="region"
            aria-label={t.conflicts}
            tabIndex={0}
          >
            {conflicts.map((conflict) => (
              <WikiConflictCard
                key={conflict.id}
                conflict={conflict}
                path={data?.path}
                labels={t}
                editDisabledReason={editDisabledReason}
                discussDisabledReason={discussDisabledReason}
                onOpenUri={onOpenUri}
                onEdit={onEdit}
                onDiscuss={onDiscuss}
              />
            ))}
          </div>
        ) : kind === 'directory' && data?.files.length ? (
          <ul className="ov-wiki-dashboard__files">
            {data.files.map((file) => (
              <li key={file}>
                <WikiFileIcon name={file} />
                <button
                  type="button"
                  className="ov-wiki-dashboard__link"
                  disabled={!onOpenDashboard && !onOpenUri}
                  onClick={() => (onOpenDashboard ?? onOpenUri)?.(file)}
                >
                  <span>{file.split('/').pop()}</span>
                  <span className="ov-wiki-dashboard__file-uri">{file}</span>
                </button>
                {onEdit ? (
                  <button type="button" onClick={() => onEdit(file)}>
                    {t.edit}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <DashboardState
            state={{ loading, error, onRetry }}
            empty={t.empty}
            loading={t.loading}
            failed={t.failed}
            retry={t.retry}
          />
        )}
        {kind === 'directory' && data?.limited ? (
          <p className="ov-wiki-dashboard__limited">{t.limited}</p>
        ) : null}
      </div>
    </div>
  );
}
