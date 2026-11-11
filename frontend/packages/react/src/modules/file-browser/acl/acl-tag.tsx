import { Button } from '../../../shared/components/button/button.js';
import { useId, useState } from 'react';
import type { FileBrowserAclLevel } from '../acl-types.js';
import { levelLabel, type AclText } from './acl-labels.js';

export function AclTag({
  level,
  text,
  onManage,
}: {
  level: FileBrowserAclLevel;
  text: AclText;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const description =
    level === 'read'
      ? text.readDescription
      : level === 'write'
        ? text.writeDescription
        : text.manageDescription;
  return (
    <div
      className="ov-file-browser-acl__tag-anchor"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault();
            setOpen(false);
          }
        }}
        type="button"
        className="ov-file-browser-acl__tag"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
      >
        {levelLabel(level, text)}
      </button>
      {open ? (
        <div id={id} className="ov-file-browser-acl__tag-detail">
          <p>{description}</p>
          <p className="ov-file-browser-acl__source">
            {text.effectivePermission}: {levelLabel(level, text)}
          </p>
          {level === 'manage' ? (
            <Button
              size="small"
              type="default"
              htmlType="button"
              onClick={() => {
                setOpen(false);
                onManage();
              }}
            >
              {text.manage}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
