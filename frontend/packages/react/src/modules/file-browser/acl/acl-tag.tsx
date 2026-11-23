import Arrow from '../../../../../icons/svg/arrow-right.svg?react';
import { Button } from '../../../shared/primitives/button/button.js';
import { Popover } from '../../../shared/primitives/popover/popover.js';
import { useEffect, useRef, useState, type RefObject } from 'react';
import type { FileBrowserAclLevel } from '../acl-types.js';
import { levelLabel, type AclText } from './acl-labels.js';

export function AclTag({
  level,
  text,
  onManage,
  triggerRef,
}: {
  level: FileBrowserAclLevel;
  text: AclText;
  onManage: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const surface = useRef<HTMLDivElement>(null);
  const focusOnOpen = useRef(false);
  const description =
    level === 'read'
      ? text.readDescription
      : level === 'write'
        ? text.writeDescription
        : text.manageDescription;
  const enter = (event: React.PointerEvent) => {
    if (event.pointerType !== 'mouse') return;
    clearTimeout(timer.current);
    setOpen(true);
  };
  const leave = () => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (!surface.current?.contains(document.activeElement)) setOpen(false);
    }, 150);
  };
  // A hover may be scheduled when this resource is replaced or unmounted.
  // Clear the browser timer even when no pointer-leave event is dispatched.
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <Popover
      open={open}
      trigger="click"
      placement="bottom"
      alignment="end"
      className="ov-file-browser-acl-tag-detail"
      onOpenChange={setOpen}
      content={
        <div
          ref={surface}
          role="presentation"

          onPointerEnter={enter}
          onPointerLeave={leave}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget))
              setOpen(false);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation();
              triggerRef.current?.focus();
              setOpen(false);
            }
          }}
        >
          <p>{description}</p>
          <p className="ov-file-browser-acl__source">
            {text.effectivePermission}: {levelLabel(level, text)}
          </p>
          {level === 'manage' ? (
            <Button
              ref={(node) => {
                if (!node || !focusOnOpen.current) return;
                focusOnOpen.current = false;
                node.focus();
              }}
              className="ov-file-browser-acl__manage"
              size="mini"
              type="text"
              htmlType="button"
              onClick={() => {
                triggerRef.current?.focus();
                setOpen(false);
                onManage();
              }}
            >
              {text.manage}
              <Arrow aria-hidden="true" width={12} height={12} />
            </Button>
          ) : null}
        </div>
      }
    >
      <button
        ref={triggerRef}
        type="button"
        className="ov-file-browser-acl__tag"
        aria-haspopup="dialog"
        onClick={(event) => {
          clearTimeout(timer.current);
          focusOnOpen.current = event.detail === 0 && !open;
        }}
        onPointerEnter={enter}
        onPointerLeave={leave}
      >
        {levelLabel(level, text)}
      </button>
    </Popover>
  );
}
