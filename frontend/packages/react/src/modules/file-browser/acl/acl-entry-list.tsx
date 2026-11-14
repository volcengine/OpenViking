import type {
  FileBrowserAclEntry,
  FileBrowserAclReport,
} from '../acl-types.js';
import { AclEntry } from './acl-entry.js';
import { levelLabel, type AclText } from './acl-labels.js';

export function AclEntryList({
  report,
  administrator,
  currentUser,
  disabled,
  saving,
  text,
  onChange,
}: {
  report: FileBrowserAclReport;
  administrator?: string;
  currentUser?: string;
  disabled: boolean;
  saving: boolean;
  text: AclText;
  onChange: (entries: FileBrowserAclEntry[]) => void;
}) {
  const entries = report.effective_entries;
  const direct = new Map(
    report.direct_entries.map(({ principal, level }) => [principal, level]),
  );
  const inherited = new Set(
    report.inherited_entries.map(({ principal }) => principal),
  );
  return (
    <ul className="ov-file-browser-acl__entries" aria-busy={saving}>
      {administrator ? (
        <AclEntry
          entry={{ principal: administrator, level: 'manage' }}
          administrator
          currentUser={currentUser === administrator}
          text={text}
          disabled
        />
      ) : (
        <li className="ov-file-browser-acl__entry">
          <span className="ov-file-browser-acl__avatar" aria-hidden="true">
            A
          </span>
          <div className="ov-file-browser-acl__identity">
            <span className="ov-file-browser-acl__name">
              {text.administrator}
            </span>
            <span className="ov-file-browser-acl__source">
              {text.administratorDescription}
            </span>
          </div>
          <span className="ov-file-browser-acl__fixed">
            {levelLabel('manage', text)}
          </span>
        </li>
      )}
      {entries
        .filter(({ principal }) => principal !== administrator)
        .map((entry) => (
          <AclEntry
            key={entry.principal}
            entry={entry}
            text={text}
            disabled={disabled}
            currentUser={entry.principal === currentUser}
            source={
              direct.get(entry.principal) === entry.level
                ? text.direct
                : inherited.has(entry.principal)
                  ? text.inherited
                  : undefined
            }
            onChange={(level) =>
              onChange(
                entries.map((item) =>
                  item.principal === entry.principal
                    ? { ...item, level }
                    : item,
                ),
              )
            }
            onRemove={() =>
              onChange(
                entries.filter((item) => item.principal !== entry.principal),
              )
            }
          />
        ))}
    </ul>
  );
}
