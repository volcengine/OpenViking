import { Button } from '../../../shared/primitives/button/button.js';
import type { AclText } from './acl-labels.js';

export function AclPermissionError({
  text,
  onRetry,
}: {
  text: AclText;
  onRetry: () => void;
}) {
  return (
    <span className="ov-file-browser-acl__lookup-error" role="status">
      {text.loadFailed}
      <Button size="small" type="default" htmlType="button" onClick={onRetry}>
        {text.retry}
      </Button>
    </span>
  );
}
