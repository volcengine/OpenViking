import { useId, useState } from 'react';
import {
  WikiEditIcon,
  WikiWarningIcon,
  WikiSuccessIcon,
} from '../../icons/index.js';
import { WikiActionButton } from './wiki-action-button.js';
import type {
  WikiConflict,
  WikiDashboardAction,
  WikiDashboardLabels,
} from './types.js';

export function WikiConflictCard({
  conflict,
  path,
  labels: t,
  actions = [],
  onOpenUri,
  onEditConflict,
}: {
  conflict: WikiConflict;
  path?: string;
  labels: Required<WikiDashboardLabels>;
  actions?: readonly WikiDashboardAction[];
  onOpenUri?: () => void;
  onEditConflict?: ((conflict: WikiConflict) => void) | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const fixed = conflict.state === 'fixed';
  const StatusIcon = fixed ? WikiSuccessIcon : WikiWarningIcon;
  const isEditAction = onEditConflict !== undefined;
  const hasFileAction = isEditAction ? !fixed : Boolean(path);
  const hasActions = hasFileAction || actions.length > 0;
  const fileAction = isEditAction
    ? onEditConflict && (() => onEditConflict(conflict))
    : onOpenUri;
  return (
    <article className="ov-wiki-dashboard__conflict">
      <div
        className={[
          'ov-wiki-dashboard__conflict-row',
          hasActions && 'ov-wiki-dashboard__conflict-row--with-actions',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        <span
          className={`ov-wiki-dashboard__status ov-wiki-dashboard__status--${conflict.state}`}
        >
          <StatusIcon width={14} height={14} />
          {fixed ? t.fixed : t.pendingLabel}
        </span>
        <div className="ov-wiki-dashboard__issue">
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={id}
            title={conflict.description}
            onClick={() => setExpanded(!expanded)}
          >
            {conflict.description}
          </button>
          <p title={conflict.reason}>{conflict.reason}</p>
          {fixed && conflict.fixPlan ? (
            <span className="ov-wiki-dashboard__solution">
              {t.solution}: {conflict.fixPlan}
            </span>
          ) : null}
        </div>
        <span
          className={`ov-wiki-dashboard__severity ov-wiki-dashboard__severity--${conflict.severity}`}
        >
          {t[conflict.severity]}
        </span>
        {hasActions ? (
          <div className="ov-wiki-dashboard__actions">
            {hasFileAction ? (
              <WikiActionButton
                icon={<WikiEditIcon width={14} height={14} />}
                tooltip={
                  isEditAction
                    ? t.editConflict(conflict.description || conflict.id)
                    : t.open
                }
                onClick={fileAction ?? undefined}
              />
            ) : null}
            {actions.map(({ key, ...action }) => (
              <WikiActionButton key={key} {...action} />
            ))}
          </div>
        ) : null}
      </div>
      <div id={id} hidden={!expanded} className="ov-wiki-dashboard__evidence">
        <dl>
          <dt>{t.reason}</dt>
          <dd>{conflict.reason || '—'}</dd>
          <dt>{t.occurred}</dt>
          <dd>
            <time dateTime={new Date(conflict.beginTime * 1000).toISOString()}>
              {new Date(conflict.beginTime * 1000).toISOString()}
            </time>
          </dd>
          <dt>{t.fixedAt}</dt>
          <dd>
            {conflict.fixTime === null ? (
              '—'
            ) : (
              <time dateTime={new Date(conflict.fixTime * 1000).toISOString()}>
                {new Date(conflict.fixTime * 1000).toISOString()}
              </time>
            )}
          </dd>
          <dt>{t.solution}</dt>
          <dd>{conflict.fixPlan || '—'}</dd>
          <dt>{t.evidence}</dt>
          <dd>{conflict.resolutionBasis || '—'}</dd>
        </dl>
      </div>
    </article>
  );
}
