import { Button } from '../../../shared/primitives/button/button.js';
import type { FileBrowserAclLevel } from '../acl-types.js';
import { AclCandidate } from './acl-candidate.js';
import type { AclText } from './acl-labels.js';
import { AclPrincipalTabs } from './acl-principal-tabs.js';
import type { useAclPrincipals } from './use-acl-principals.js';

type PrincipalType = 'user' | 'group';

export function AclPrincipalPicker({
  id,
  directory,
  supported,
  type,
  candidates,
  pending,
  hasKeyword,
  canAdd,
  text,
  onTypeChange,
  onLevelChange,
  onCancel,
  onAdd,
}: {
  id: string;
  directory: ReturnType<typeof useAclPrincipals>;
  supported: readonly PrincipalType[];
  type: PrincipalType;
  candidates: string[];
  pending: Record<string, FileBrowserAclLevel>;
  hasKeyword: boolean;
  canAdd: boolean;
  text: AclText;
  onTypeChange: (type: PrincipalType) => void;
  onLevelChange: (principal: string, level?: FileBrowserAclLevel) => void;
  onCancel: () => void;
  onAdd: () => void;
}) {
  return (
    <div id={id} role="dialog" aria-label={text.addPrincipal}>
      <AclPrincipalTabs
        supported={supported}
        type={type}
        text={text}
        onChange={onTypeChange}
      />
      <div
        className="ov-file-browser-acl__candidates"
        aria-busy={directory.loading}
      >
        {candidates.map((principal) => (
          <AclCandidate
            key={principal}
            principal={principal}
            level={pending[principal]}
            text={text}
            onChange={(level) => onLevelChange(principal, level)}
          />
        ))}
        {!directory.loading && !candidates.length && !directory.error ? (
          <p className="ov-file-browser-acl__hint">
            {hasKeyword ? text.noMatches : text.empty}
          </p>
        ) : null}
      </div>
      {directory.error ? (
        <div className="ov-file-browser-acl__failure" role="alert">
          <p>{text.principalsFailed}</p>
          <p>{directory.error.message}</p>
          <Button
            size="small"
            type="default"
            htmlType="button"
            onClick={directory.retry}
          >
            {text.retry}
          </Button>
        </div>
      ) : null}
      {directory.loading ? (
        <p className="ov-file-browser-acl__hint">{text.loading}</p>
      ) : null}
      {directory.hasMore ? (
        <Button
          size="small"
          type="default"
          htmlType="button"
          className="ov-file-browser-acl__more"
          long
          disabled={directory.loading}
          onClick={directory.more}
        >
          {text.loadMore}
        </Button>
      ) : null}
      <div className="ov-file-browser-acl__buttons">
        <Button
          size="small"
          type="default"
          htmlType="button"
          onClick={onCancel}
        >
          {text.cancel}
        </Button>
        <Button
          size="small"
          type="primary"
          htmlType="button"
          disabled={!canAdd}
          onClick={onAdd}
        >
          {text.add}
        </Button>
      </div>
    </div>
  );
}
