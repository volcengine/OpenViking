import { Button } from '../../../shared/primitives/button/button.js';
import type { InteractionLabels } from './labels.js';

export function InteractionActions({
  labels,
  disabled,
  submitting,
  onCancel,
  onPrevious,
  submitDisabled,
}: {
  labels: InteractionLabels;
  disabled: boolean;
  submitting: boolean;
  onCancel?: () => void;
  onPrevious?: () => void;
  submitDisabled?: boolean;
}) {
  return (
    <div className="ov-viking-bot__interaction-footer">
      {onPrevious ? (
        <Button type="outline" disabled={disabled} onClick={onPrevious}>
          {labels.previous}
        </Button>
      ) : null}
      {onCancel ? (
        <Button
          type="outline"
          className="ov-viking-bot__interaction-action"
          disabled={disabled}
          onClick={onCancel}
        >
          {labels.cancel}
        </Button>
      ) : null}
      <Button
        type="primary"
        htmlType="submit"
        className="ov-viking-bot__interaction-action"
        loading={submitting}
        disabled={disabled || submitDisabled}
      >
        {labels.submit}
      </Button>
    </div>
  );
}
