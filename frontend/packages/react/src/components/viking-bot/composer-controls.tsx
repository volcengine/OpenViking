import type { SenderProps } from '@ark-design/ui/sender';
import type { ButtonHTMLAttributes } from 'react';
import SendSvg from '../../../../icons/svg/viking-bot/send.svg?react';
import AttachSvg from '../../../../icons/svg/viking-bot/attach.svg?react';
import { Tooltip } from '../../shared/components/tooltip/index.js';

type SubmitProps = Parameters<NonNullable<SenderProps['renderSubmit']>>[0];

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
      <button
        {...props}
        type="button"
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
      </button>
    </Tooltip>
  );
}

export function VikingBotAttach({
  label,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <Tooltip content={label}>
      <button
        {...props}
        type="button"
        className="ov-viking-bot__attach"
        aria-label={label}
      >
        <AttachSvg aria-hidden="true" focusable="false" />
      </button>
    </Tooltip>
  );
}
