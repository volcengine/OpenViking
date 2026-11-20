import { useId, useState } from 'react';
import type { OvRequestOptions } from '../../client/types.js';
import { Button } from '../../shared/primitives/button/button.js';
import { Checkbox } from '../../shared/primitives/checkbox/checkbox.js';
import { formatVikingUriLabel } from '../../shared/path-label.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { FileBrowserSearchScopeTree } from './file-browser-search-scope-tree.js';
import type { ScopeOption } from './search-types.js';

export function FileBrowserSearchScopeDraft({
  id,
  scopes,
  value,
  multiple,
  text,
  requestOptions,
  onCancel,
  onConfirm,
}: {
  id: string;
  scopes: readonly ScopeOption[];
  value: readonly string[];
  multiple: boolean;
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  onCancel: () => void;
  onConfirm: (value: string[]) => void;
}) {
  const [draft, setDraft] = useState([...value]);
  const name = useId();
  const all = scopes.every(({ uri }) => draft.includes(uri));
  function change(uri: string, checked: boolean) {
    if (!multiple) {
      setDraft(checked ? [uri] : []);
      return;
    }
    // A parent covers its subtree. The search API has no exclusions, so inherited
    // child checks remain disabled until that parent is deselected.
    setDraft(
      checked
        ? [
            ...draft.filter(
              (item) => item !== uri && !item.startsWith(`${uri}/`),
            ),
            uri,
          ]
        : draft.filter((item) => item !== uri),
    );
  }

  return (
    <div
      id={id}
      className="ov-file-browser-search__scope-menu"
      role="group"
      aria-label={text.searchPickScope}
    >
      <ul className="ov-file-browser-search__scope-tree">
        {scopes.map(({ uri }) => (
          <FileBrowserSearchScopeTree
            key={uri}
            uri={uri}
            label={formatVikingUriLabel(uri)}
            value={draft}
            multiple={multiple}
            name={name}
            text={text}
            requestOptions={requestOptions}
            onChange={change}
          />
        ))}
      </ul>
      <div className="ov-file-browser-search__scope-footer">
        {multiple ? (
          <Checkbox
            checked={all}
            indeterminate={!all && draft.length > 0}
            onChange={(checked) =>
              setDraft(checked ? scopes.map(({ uri }) => uri) : [])
            }
          >
            {text.searchAllScopes}
          </Checkbox>
        ) : null}
        <div className="ov-file-browser-search__scope-actions">
          <Button size="small" onClick={onCancel}>
            {text.searchCancelScopes}
          </Button>
          <Button size="small" type="primary" onClick={() => onConfirm(draft)}>
            {text.searchConfirmScopes}
          </Button>
        </div>
      </div>
    </div>
  );
}
