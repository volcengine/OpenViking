import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';
import type { TuiRecord } from '../../components/tui/index.js';
import { ConsoleIcon } from '../../icons/console-icons.js';
import { Tooltip } from '../../shared/components/tooltip/index.js';
import { useOverlayPosition } from '../../shared/components/overlay/use-overlay-position.js';
import type { consoleLabels } from './labels.js';

const subscribe = () => () => {};
const clientSnapshot = () => true;
const serverSnapshot = () => false;

function historyTime(record: TuiRecord, locale: 'en' | 'zh') {
  const date = new Date(record.createdAtMs ?? NaN);
  if (!Number.isFinite(date.getTime())) return record.createdAt ?? '';
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat(locale, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(date)
      .map(({ type, value }) => [type, value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

export function ConsoleHistory({
  records,
  isExecuting,
  locale,
  labels,
  onRecall,
}: {
  records: readonly TuiRecord[];
  isExecuting: boolean;
  locale: 'en' | 'zh';
  labels: ReturnType<typeof consoleLabels>;
  onRecall: (command: string) => void;
}) {
  const mounted = useSyncExternalStore(
    subscribe,
    clientSnapshot,
    serverSnapshot,
  );
  const native =
    mounted &&
    'popover' in HTMLElement.prototype &&
    typeof HTMLElement.prototype.showPopover === 'function';
  const [open, setOpen] = useState(false);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLDivElement>(null);
  const id = useId();
  const position = useOverlayPosition(open, trigger, surface, 'bottom', 'end');
  const close = () => {
    if (native) surface?.hidePopover();
    else setOpen(false);
  };
  // Effect necessity: native popovers can close via Escape or light dismiss,
  // independently of React events. Subscribe to the browser toggle event to
  // position the open surface; remove the subscription when it is replaced.
  useEffect(() => {
    if (!native || !surface) return;
    const onToggle = () => setOpen(surface.matches(':popover-open'));
    surface.addEventListener('toggle', onToggle);
    return () => surface.removeEventListener('toggle', onToggle);
  }, [native, surface]);
  // Effect necessity: browsers without Popover need document-level outside-click
  // and Escape subscriptions, including for this portalled surface. User events
  // on the trigger cannot observe them; closing/unmounting removes both listeners.
  useEffect(() => {
    if (native || !open) return;
    const outside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!trigger.current?.contains(target) && !surface?.contains(target))
        setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      trigger.current?.querySelector('button')?.focus();
    };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [native, open, surface]);
  const invoker = native
    ? 'commandForElement' in HTMLButtonElement.prototype
      ? { commandfor: id, command: 'toggle-popover' }
      : { popovertarget: id }
    : {};
  return (
    <div className="ov-console__history" ref={trigger}>
      <Tooltip content={labels.history} className="ov-console__action-tooltip">
        <button
          {...invoker}
          type="button"
          className="ov-console__action"
          aria-label={labels.history}
          aria-haspopup="dialog"
          aria-controls={id}
          aria-expanded={open}
          data-open={open || undefined}
          onClick={native ? undefined : () => setOpen(!open)}
        >
          <ConsoleIcon name="history" />
        </button>
      </Tooltip>
      {mounted
        ? createPortal(
            <div
              ref={setSurface}
              id={id}
              popover="auto"
              role="dialog"
              aria-label={labels.history}
              hidden={!native && !open}
              className="ov-tooltip ov-console__history-content"
              style={{ top: position.top, left: position.left }}
            >
              {records.length ? (
                <ul>
                  {[...records].reverse().map((record) => (
                    <li key={record.id}>
                      <button
                        type="button"
                        disabled={isExecuting}
                        onClick={() => {
                          close();
                          onRecall(record.command);
                        }}
                      >
                        <span className="ov-console__history-command">
                          {record.command}
                        </span>
                        <span className="ov-console__history-time">
                          {historyTime(record, locale)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="ov-console__history-empty">
                  <span className="ov-console__history-illustration">
                    <ConsoleIcon name="historyEmpty" />
                  </span>
                  <span>{labels.emptyHistory}</span>
                </div>
              )}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
