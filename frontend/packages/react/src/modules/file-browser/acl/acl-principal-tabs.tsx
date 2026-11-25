import { Button } from '../../../shared/primitives/button/button.js';
import type { AclText } from './acl-labels.js';

export function AclPrincipalTabs({
  supported,
  type,
  text,
  onChange,
}: {
  supported: readonly ('user' | 'group')[];
  type: 'user' | 'group';
  text: AclText;
  onChange: (type: 'user' | 'group') => void;
}) {
  return (
    <div
      className="ov-file-browser-acl__types"
      role="group"
      aria-label={text.addPrincipal}
    >
      {supported.map((value) => (
        <Button
          key={value}
          size="small"
          type={type === value ? 'primary' : 'secondary'}
          aria-pressed={type === value}
          onClick={() => onChange(value)}
        >
          {value === 'user' ? text.users : text.groups}
        </Button>
      ))}
    </div>
  );
}
