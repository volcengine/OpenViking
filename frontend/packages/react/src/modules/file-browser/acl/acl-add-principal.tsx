import { AclPrincipalTabs } from './acl-principal-tabs.js';
import { Button } from '../../../shared/components/button/button.js';
import { Input } from '../../../shared/components/input/input.js';
import { SearchIcon } from '../file-browser-icons.js';
import { useId, useState } from 'react';
import type { OvIdentityValue } from '../../../client/types.js';
import type {
  FileBrowserAclContext,
  FileBrowserAclEntry,
  FileBrowserAclLevel,
  FileBrowserAclOptions,
} from '../acl-types.js';
import { AclCandidate } from './acl-candidate.js';
import type { AclText } from './acl-labels.js';
import { useAclPrincipals } from './use-acl-principals.js';

export function AclAddPrincipal({
  entries,
  text,
  disabled,
  context,
  accountId,
  options,
  onAdd,
}: {
  entries: FileBrowserAclEntry[];
  text: AclText;
  disabled: boolean;
  context: FileBrowserAclContext;
  accountId?: OvIdentityValue;
  options?: FileBrowserAclOptions;
  onAdd: (entries: FileBrowserAclEntry[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [type, setType] = useState<'user' | 'group'>('user');
  const [pending, setPending] = useState<Record<string, FileBrowserAclLevel>>(
    {},
  );
  const id = useId();
  const directory = useAclPrincipals({
    open: open && !disabled,
    type,
    keyword,
    context,
    accountId,
    options,
  });
  const existing = new Set(entries.map((entry) => entry.principal));
  const candidates = [
    ...new Set([...(type === 'user' ? ['user:*'] : []), ...directory.items]),
  ]
    .filter(
      (principal) =>
        !existing.has(principal) && principal.startsWith(`${type}:`),
    )
    .filter((principal) =>
      (principal === 'user:*'
        ? text.allUsers
        : principal.slice(principal.indexOf(':') + 1)
      )
        .toLocaleLowerCase()
        .includes(keyword.trim().toLocaleLowerCase()),
    );
  const reset = () => {
    setOpen(false);
    setKeyword('');
    setPending({});
  };
  const selected = Object.entries(pending).filter(
    ([principal]) => !existing.has(principal),
  );
  return (
    <div className="ov-file-browser-acl__add">
      <label htmlFor={id}>{text.addPrincipal}</label>
      <Input
        prefix={<SearchIcon />}
        id={id}
        type="search"
        role="combobox"
        aria-haspopup="dialog"
        value={keyword}
        placeholder={text.search}
        disabled={disabled}
        aria-expanded={open && !disabled}
        aria-controls={open ? `${id}-options` : undefined}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setKeyword(event.target.value);
          setOpen(true);
        }}
      />
      {open && !disabled ? (
        <div
          id={`${id}-options`}
          className="ov-file-browser-acl__picker"
          role="dialog"
          aria-label={text.addPrincipal}
        >
          <AclPrincipalTabs type={type} text={text} onChange={setType} />
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
                onChange={(level) =>
                  setPending((previous) => {
                    const next = { ...previous };
                    if (level) next[principal] = level;
                    else delete next[principal];
                    return next;
                  })
                }
              />
            ))}
            {!directory.loading && !candidates.length && !directory.error ? (
              <p className="ov-file-browser-acl__hint">
                {keyword.trim() ? text.noMatches : text.empty}
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
              onClick={reset}
            >
              {text.cancel}
            </Button>
            <Button
              size="small"
              type="primary"
              htmlType="button"
              className="ov-file-browser-acl__primary"
              disabled={!selected.length}
              onClick={() => {
                onAdd(
                  selected.map(([principal, level]) => ({ principal, level })),
                );
                reset();
              }}
            >
              {text.add}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
