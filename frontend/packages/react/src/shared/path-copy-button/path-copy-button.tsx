import { useCallback, useEffect, useRef, useState, type Ref } from 'react';
import { CheckIcon } from '../../icons/index.js';
import { Popover } from '../primitives/popover/popover.js';
import { useToast } from '../primitives/toast/use-toast.js';
import { PathCopyIcon } from './path-copy-icon.js';
import { useCopyFeedbackArrow } from './use-copy-feedback-arrow.js';

export interface PathCopyButtonProps {
  label: string;
  successLabel: string;
  failureLabel: string;
  disabled?: boolean;
  buttonRef?: Ref<HTMLButtonElement>;
  onClick: () => Promise<boolean>;
}

/** Instance-local feedback shared by current and historical node paths. */
export function PathCopyButton({
  label,
  successLabel,
  failureLabel,
  disabled = false,
  buttonRef,
  onClick,
}: PathCopyButtonProps) {
  const [toast, toastHolder] = useToast();
  const [copyCount, setCopyCount] = useState(0);
  const [keyboardFocus, setKeyboardFocus] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const anchor = useRef<HTMLButtonElement | null>(null);
  const feedbackRef = useCopyFeedbackArrow(anchor);
  const attachButton = useCallback(
    (node: HTMLButtonElement | null) => {
      anchor.current = node;
      if (typeof buttonRef === 'function') buttonRef(node);
      else if (buttonRef) buttonRef.current = node;
    },
    [buttonRef],
  );
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const lifetime = useRef({ mounted: false, pending: false, request: 0 });
  const copied = copyCount > 0;
  const message =
    successLabel && copyCount % 2 === 0
      ? `${successLabel}\u00a0`
      : successLabel;
  // Effect necessity: clipboard permission can resolve after this path unmounts,
  // and the click handler owns a browser feedback timer. Cleanup invalidates late
  // results and releases that timer; neither can be cleaned by another render.
  useEffect(() => {
    const current = lifetime.current;
    current.mounted = true;
    return () => {
      current.mounted = false;
      current.request++;
      clearTimeout(timer.current);
    };
  }, []);
  const copy = async () => {
    const current = lifetime.current;
    if (disabled || current.pending) return;
    setDismissed(false);
    current.pending = true;
    const request = ++current.request;
    clearTimeout(timer.current);
    setCopyCount(0);
    let copied = false;
    try {
      copied = await onClick();
    } catch {
      /* The instance reports clipboard rejection below. */
    } finally {
      current.pending = false;
    }
    if (!current.mounted || request !== current.request) return;
    if (!copied) {
      toast.error({ id: 'copy-path', content: failureLabel });
      return;
    }
    toast.clear();
    setCopyCount(request);
    timer.current = setTimeout(() => setCopyCount(0), 1500);
  };
  return (
    <>
      {toastHolder}
      <span className="ov-path-copy-status" role="status">
        {copied ? message : ''}
      </span>
      <Popover
        placement="top"
        role={copied ? undefined : 'tooltip'}
        ariaHidden={copied}
        open={dismissed ? false : copied || keyboardFocus ? true : undefined}
        disabled={disabled}
        className={copied ? 'ov-path-copy-feedback' : 'ov-path-copy-hint'}
        content={
          copied ? (
            <span ref={feedbackRef} className="ov-path-copy-feedback__content">
              <CheckIcon
                width={14}
                height={14}
                className="ov-path-copy-feedback__icon"
                aria-hidden="true"
              />
              <span>{message}</span>
            </span>
          ) : (
            label
          )
        }
      >
        <button
          ref={attachButton}
          type="button"
          className="ov-path-copy-button"
          aria-label={label}
          disabled={disabled}
          data-copied={copied || undefined}
          onMouseEnter={() => setDismissed(false)}
          onFocus={(event) => {
            setDismissed(false);
            setKeyboardFocus(event.currentTarget.matches(':focus-visible'));
          }}
          onBlur={() => setKeyboardFocus(false)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setDismissed(true);
          }}
          onClick={() => void copy()}
        >
          {copied ? (
            <CheckIcon width={14} height={14} aria-hidden="true" />
          ) : (
            <PathCopyIcon />
          )}
        </button>
      </Popover>
    </>
  );
}
