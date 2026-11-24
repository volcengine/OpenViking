import type { FileBrowserAclEntry, FileBrowserAclLevel } from '../acl-types.js';
import type { AclText } from './acl-labels.js';
import { AclMenu } from './acl-menu.js';

export function AclAvatar({ principal }: { principal: string }) {
  const [type, id] = principal.split(':');
  return (
    <span
      aria-hidden="true"
      className="ov-file-browser-acl__avatar"
      data-group={type === 'group' || undefined}
    >
      {type === 'group' ? 'G' : id?.charAt(0).toUpperCase()}
    </span>
  );
}
export function AclEntry({
  entry,
  administrator,
  currentUser,
  source,
  text,
  disabled,
  onChange,
  onRemove,
}: {
  entry: FileBrowserAclEntry;
  source?: string;
  text: AclText;
  disabled: boolean;
  administrator?: boolean;
  currentUser?: boolean;
  onChange?: (level: FileBrowserAclLevel) => void;
  onRemove?: () => void;
}) {
  const name =
    entry.principal === 'user:*'
      ? text.allUsers
      : entry.principal.slice(entry.principal.indexOf(':') + 1);
  return (
    <li className="ov-file-browser-acl__entry">
      <AclAvatar principal={entry.principal} />
      <div className="ov-file-browser-acl__identity">
        <div className="ov-file-browser-acl__name-line">
          <span className="ov-file-browser-acl__name" title={name}>
            {name}
          </span>
          {administrator ? (
            <span className="ov-file-browser-acl__badge">
              {text.administratorBadge}
            </span>
          ) : null}
          {currentUser ? (
            <span className="ov-file-browser-acl__badge ov-file-browser-acl__badge--current">
              {text.currentUserBadge}
            </span>
          ) : null}
        </div>
        {source ? (
          <span className="ov-file-browser-acl__source">{source}</span>
        ) : null}
      </div>
      {onChange ? (
        <AclMenu
          level={entry.level}
          text={text}
          name={entry.principal}
          disabled={disabled}
          onChange={onChange}
          onRemove={onRemove}
        />
      ) : null}
    </li>
  );
}
