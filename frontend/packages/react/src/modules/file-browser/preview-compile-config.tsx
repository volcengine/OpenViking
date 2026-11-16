import { useCallback, useState } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
import { Button } from '../../shared/primitives/button/button.js';
import ChevronIcon from '../../../../icons/svg/chevron-right.svg?react';
import { FolderIcon } from '../../icons/index.js';
import { readCompileConfig } from './preview-native.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { PreviewQueryState } from './preview-query-state.js';
import { PreviewCompileEdit } from './preview-compile-edit.js';
import type {
  CompileConfig,
  PreviewWorkflowProps,
} from './preview-workflow-types.js';

export type CompileField = 'from' | 'skill' | 'update_schedule';
type Props = PreviewWorkflowProps & {
  canWrite: boolean;
  loadingText: string;
  retryText: string;
};

export function PreviewCompileConfig(props: Props) {
  const query = useDashboardQuery(
    useCallback(
      (client: OvClient, options: OvRequestOptions) =>
        readCompileConfig(client, props.uri, options),
      [props.uri],
    ),
    props.requestOptions,
  );
  const [saved, setSaved] = useState<CompileConfig>();
  const [editing, setEditing] = useState<CompileField>();
  const config = saved ?? query.data;
  const { text } = props;
  const rule = config?.auto_update ? config.update_schedule : null;
  const time = rule
    ? `${String(rule.hour).padStart(2, '0')}:${String(rule.minute).padStart(2, '0')}`
    : '';
  const weekday =
    rule?.frequency === 'weekly'
      ? new Intl.DateTimeFormat(props.locale, {
          weekday: 'long',
          timeZone: 'UTC',
        }).format(new Date(Date.UTC(2024, 0, rule.weekday ?? 1)))
      : '';
  const row = (label: string, value: string, field?: CompileField) => (
    <div className="ov-compile-config__row">
      <span>{label}</span>
      <Tooltip
        content={
          field === 'from' ? (
            <div className="ov-compile-config__paths">
              {config?.from.map((path) => (
                <div key={path}>{path}</div>
              ))}
            </div>
          ) : (
            value
          )
        }
        disabled={field === 'update_schedule'}
      >
        {/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- Read-only paths expose their full text on focus. */}
        <span
          className="ov-compile-config__value"
          tabIndex={
            field === 'update_schedule' || (field && props.canWrite)
              ? undefined
              : 0
          }
        >
          {field ? (
            <Button
              type="outline"
              disabled={!props.canWrite}
              aria-label={label}
              onClick={() => setEditing(field)}
            >
              {field !== 'update_schedule' ? (
                <FolderIcon width={16} height={16} />
              ) : null}
              <span className="ov-compile-config__path">{value}</span>
              <ChevronIcon
                className="ov-compile-config__chevron"
                width={12}
                height={12}
                aria-hidden="true"
              />
            </Button>
          ) : (
            <span className="ov-compile-config__target">
              <FolderIcon width={16} height={16} />
              <span>{value}</span>
            </span>
          )}
        </span>
        {/* eslint-enable jsx-a11y/no-noninteractive-tabindex */}
      </Tooltip>
    </div>
  );
  return (
    <>
      <PreviewQueryState
        query={query}
        text={{ ...text, loading: props.loadingText, retry: props.retryText }}
      />
      {config ? (
        <>
          {saved ? <p role="status">{text.saved}</p> : null}
          <div className="ov-compile-config__rows">
            {row(text.sources, config.from.join('、'), 'from')}
            {row(text.target, config.to)}
            {row(text.skill, config.skill, 'skill')}
            {row(
              text.schedule,
              rule
                ? [
                    rule.frequency === 'daily' ? text.daily : weekday,
                    time,
                  ].join(' ')
                : text.noSchedule,
              'update_schedule',
            )}
          </div>
          <p className="ov-compile-config__hint">{text.configurationHint}</p>
          {editing ? (
            <PreviewCompileEdit
              {...props}
              initial={config}
              field={editing}
              onCancel={() => setEditing(undefined)}
              onSaved={(next) => {
                setSaved(next);
                setEditing(undefined);
                props.onUpdated();
              }}
            />
          ) : null}
        </>
      ) : null}
    </>
  );
}
