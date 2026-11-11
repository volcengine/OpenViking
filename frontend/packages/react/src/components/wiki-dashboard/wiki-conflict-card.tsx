import { useId, useState } from 'react';
import {
  WikiWarningIcon,
  WikiSuccessIcon,
  WikiEditIcon,
  WikiDiscussIcon,
  WikiHistoryIcon,
} from '../../icons/index.js';
import { WikiFileIcon } from './wiki-file-icon.js';
import { WikiActionButton } from './wiki-action-button.js';
import type {
  WikiConflict,
  WikiDashboardProps,
  WikiDashboardLabels,
} from './types.js';

export function WikiConflictCard({
  conflict,
  path,
  labels: t,
  editDisabledReason,
  discussDisabledReason,
  onOpenUri,
  onEdit,
  onDiscuss,
}: {
  conflict: WikiConflict;
  path?: string;
  labels: WikiDashboardLabels;
  editDisabledReason?: string;
  discussDisabledReason?: string;
  onOpenUri?: () => void;
  onEdit?: Extract<WikiDashboardProps, { kind: 'file' }>['onEdit'];
  onDiscuss?: Extract<WikiDashboardProps, { kind: 'file' }>['onDiscuss'];
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const fixed = conflict.state === 'fixed';
  const StatusIcon = fixed ? WikiSuccessIcon : WikiWarningIcon;
  return (
    <article className="ov-wiki-dashboard__conflict">
      <div
        className={`ov-wiki-dashboard__conflict-row${path ? '' : ' ov-wiki-dashboard__conflict-row--without-path'}`}
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
        {path ? (
          <div className="ov-wiki-dashboard__path">
            <WikiFileIcon name={path} />
            <button
              type="button"
              className="ov-wiki-dashboard__link"
              title={path}
              disabled={!onOpenUri}
              onClick={() => onOpenUri?.()}
            >
              {path}
            </button>
          </div>
        ) : null}
        <span
          className={`ov-wiki-dashboard__severity ov-wiki-dashboard__severity--${conflict.severity}`}
        >
          {t[conflict.severity]}
        </span>
        <div className="ov-wiki-dashboard__actions">
          {fixed ? (
            <WikiActionButton
              label={t.history}
              hint={t.historyHint}
              aria-expanded={expanded}
              aria-controls={id}
              onClick={() => setExpanded(!expanded)}
            >
              <WikiHistoryIcon width={14} height={14} />
            </WikiActionButton>
          ) : (
            <>
              <WikiActionButton
                label={`${t.edit}: ${conflict.description}`}
                hint={onEdit ? t.edit : editDisabledReason || t.unavailable}
                onClick={onEdit ? () => onEdit(conflict) : undefined}
              >
                <WikiEditIcon width={14} height={14} />
              </WikiActionButton>
              <WikiActionButton
                label={`${t.discuss}: ${conflict.description}`}
                hint={
                  onDiscuss ? t.discuss : discussDisabledReason || t.unavailable
                }
                onClick={onDiscuss ? () => onDiscuss(conflict) : undefined}
              >
                <WikiDiscussIcon width={14} height={14} />
              </WikiActionButton>
            </>
          )}
        </div>
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
