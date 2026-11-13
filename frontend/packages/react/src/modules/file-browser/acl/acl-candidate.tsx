import { Checkbox } from '../../../shared/primitives/checkbox/checkbox.js';
import type { FileBrowserAclLevel } from '../acl-types.js';
import { AclAvatar } from './acl-entry.js';
import { AclMenu } from './acl-menu.js';
import type { AclText } from './acl-labels.js';

export function AclCandidate({
  principal,
  level,
  text,
  onChange,
}: {
  principal: string;
  level?: FileBrowserAclLevel;
  text: AclText;
  onChange: (level?: FileBrowserAclLevel) => void;
}) {
  const name =
    principal === 'user:*'
      ? text.allUsers
      : principal.slice(principal.indexOf(':') + 1);
  return (
    <div className="ov-file-browser-acl__candidate">
      <Checkbox
        checked={level !== undefined}
        onChange={(next) => onChange(next ? 'read' : undefined)}
      >
        <AclAvatar principal={principal} />
        <span>{name}</span>
      </Checkbox>
      <AclMenu
        text={text}
        name={principal}
        level={level ?? 'read'}
        onChange={onChange}
      />
    </div>
  );
}
