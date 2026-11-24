import { useId } from 'react';
import { useLocale } from '../../../shared/locale-context.js';
import { resolveLocale, type Locale } from '../../../shared/locale.js';
import { Button } from '../../../shared/primitives/button/button.js';
import { VikingBotInteraction } from '../interaction/interaction-boundary.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';
import type {
  VikingBotInteractionProps,
  VikingBotInteractionRenderContext,
} from '../interaction/types.js';
import type {
  VikingBotConfirmationLabels,
  VikingBotConfirmationProps,
} from './types.js';

type ConfirmationFormProps = Pick<
  VikingBotConfirmationProps,
  'title' | 'rows' | 'onEdit' | 'onConfirm'
>;

function ConfirmationForm({
  context,
  title,
  rows,
  labels,
  onEdit,
  onConfirm,
}: ConfirmationFormProps & {
  context: VikingBotInteractionRenderContext;
  labels: VikingBotConfirmationLabels;
}) {
  const titleId = useId();
  return (
    <form
      className="ov-viking-bot__interaction-card ov-viking-bot__confirmation"
      aria-labelledby={titleId}
      aria-busy={context.submitting || undefined}
      onSubmit={(event) => {
        event.preventDefault();
        void context.submit(undefined);
      }}
    >
      <h3 id={titleId} className="ov-viking-bot__interaction-title">
        {title ?? labels.title}
      </h3>
      <dl className="ov-viking-bot__confirmation-values">
        {rows.map((row, index) => (
          <div key={index}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
      <div className="ov-viking-bot__interaction-footer">
        {onEdit && (
          <Button
            className="ov-viking-bot__interaction-action"
            type="outline"
            disabled={context.disabled}
            onClick={() => {
              context.clearError();
              onEdit();
            }}
          >
            {labels.edit}
          </Button>
        )}
        <Button
          className="ov-viking-bot__interaction-action"
          type="primary"
          htmlType="submit"
          disabled={context.disabled || !onConfirm}
          loading={context.submitting}
        >
          {labels.confirm}
        </Button>
      </div>
    </form>
  );
}

export function confirmationInteraction({
  title,
  rows,
  locale,
  labels: overrides,
  onEdit,
  onConfirm,
  ...props
}: VikingBotConfirmationProps & { locale: Locale }): VikingBotInteractionProps {
  const labels = resolveLocale(
    { 'en-US': enUS, 'zh-CN': zhCN },
    locale,
    overrides,
  );
  return {
    ...props,
    locale,
    labels: { title: labels.title, submitFailed: labels.submitFailed },
    onSubmit: onConfirm,
    request: {
      kind: 'custom',
      render: (context) => (
        <ConfirmationForm
          {...{ context, title, rows, labels, onEdit, onConfirm }}
        />
      ),
    },
  };
}

/** Display explicit configuration rows and report the host's edit/confirm actions. */
export function VikingBotConfirmation({
  locale: localeProp,
  ...props
}: VikingBotConfirmationProps) {
  const locale = useLocale(localeProp);
  return (
    <VikingBotInteraction {...confirmationInteraction({ ...props, locale })} />
  );
}
