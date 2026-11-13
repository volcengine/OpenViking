import { Button } from '../../shared/primitives/button/button.js';
import type { SenderProps } from '@ark-design/ui/sender';
import type { ButtonHTMLAttributes } from 'react';
import SendSvg from '../../../../icons/svg/viking-bot/send.svg?react';
import AttachSvg from '../../../../icons/svg/viking-bot/attach.svg?react';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';

type SubmitProps = Parameters<NonNullable<SenderProps['renderSubmit']>>[0];

export function VikingBotSubmitActions({
  props,
  streaming,
  canSteer,
  onCancel,
  labels,
}: {
  props: SubmitProps;
  streaming?: boolean;
  canSteer?: boolean;
  onCancel?: () => void;
  labels: { send: string; stop: string };
}) {
  if (!streaming)
    return <VikingBotSubmit {...props} sending={false} label={labels.send} />;
  if (!canSteer)
    return onCancel ? (
      <VikingBotSubmit
        {...props}
        sending
        onStop={onCancel}
        label={labels.stop}
      />
    ) : (
      <VikingBotSubmit
        {...props}
        sending={false}
        disabled
        label={labels.send}
      />
    );
  return (
    <div className="ov-viking-bot__submit-actions">
      {onCancel ? (
        <VikingBotSubmit sending onStop={onCancel} label={labels.stop} />
      ) : null}
      <VikingBotSubmit {...props} sending={false} label={labels.send} />
    </div>
  );
}

export function VikingBotSubmit({
  onSend,
  onStop,
  sending,
  tip,
  className,
  label,
  ...props
}: SubmitProps & { label: string }) {
  return (
    <Tooltip content={tip ?? label}>
      <Button
        {...props}
        size="default"
        type="primary"
        shape="circle"
        iconOnly
        htmlType="button"
        className={['ov-viking-bot__submit', className]
          .filter(Boolean)
          .join(' ')}
        aria-label={label}
        onClick={(event) =>
          void (sending ? onStop : onSend)?.(event.nativeEvent)
        }
      >
        {sending ? (
          <span className="ov-viking-bot__stop-icon" aria-hidden="true" />
        ) : (
          <SendSvg aria-hidden="true" focusable="false" />
        )}
      </Button>
    </Tooltip>
  );
}

export function VikingBotAttach({
  label,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <Tooltip content={label}>
      <Button
        {...props}
        size="mini"
        type="text"
        iconOnly
        htmlType="button"
        className="ov-viking-bot__attach"
        aria-label={label}
      >
        <AttachSvg aria-hidden="true" focusable="false" />
      </Button>
    </Tooltip>
  );
}
