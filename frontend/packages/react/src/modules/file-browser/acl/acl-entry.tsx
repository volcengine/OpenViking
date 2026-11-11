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
  onChange: (level: FileBrowserAclLevel) => void;
  onRemove: () => void;
}) {
  const name =
    entry.principal === 'user:*'
      ? text.allUsers
      : entry.principal.slice(entry.principal.indexOf(':') + 1);
  return (
    <li className="ov-file-browser-acl__entry">
      <AclAvatar principal={entry.principal} />
      <div className="ov-file-browser-acl__identity">
        <span className="ov-file-browser-acl__name">{name}</span>
        <span className="ov-file-browser-acl__source">{source}</span>
      </div>
      <AclMenu
        level={entry.level}
        text={text}
        name={entry.principal}
        disabled={disabled}
        onChange={onChange}
        onRemove={onRemove}
      />
    </li>
  );
}
