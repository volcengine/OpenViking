import { useId, useState } from 'react';
import FolderIcon from '../../../../icons/svg/file-browser/search-folder-open.svg?react';
import ChevronIcon from '../../../../icons/svg/file-browser/search-chevron-down.svg?react';
import treeEn from '../../components/file-tree/locales/en-US.js';
import treeZh from '../../components/file-tree/locales/zh-CN.js';
import type { OvRequestOptions } from '../../client/types.js';
import { useLocale } from '../../shared/locale-context.js';
import { Checkbox } from '../../shared/primitives/checkbox/checkbox.js';
import { Radio } from '../../shared/primitives/radio/radio.js';
import { displayNameFromUri } from '../../shared/directory-tree/rows.js';
import { formatVikingUriLabel } from '../../shared/path-label.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { useSearchScopePage } from './use-search-scope-page.js';

interface ScopeTreeProps {
  uri: string;
  label: string;
  value: readonly string[];
  multiple: boolean;
  name: string;
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  onChange: (uri: string, checked: boolean) => void;
}

export function FileBrowserSearchScopeTree({
  uri,
  label,
  value,
  multiple,
  name,
  text,
  requestOptions,
  onChange,
}: ScopeTreeProps) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const treeText = useLocale() === 'zh-CN' ? treeZh : treeEn;
  const { page, load, cancel } = useSearchScopePage(
    expanded ? uri : undefined,
    requestOptions,
  );
  const inherited =
    multiple && value.some((item) => uri.startsWith(`${item}/`));
  const checked = inherited || value.includes(uri);
  const mixed = !checked && value.some((item) => item.startsWith(`${uri}/`));
  const content = (
    <>
      <FolderIcon aria-hidden="true" />
      <span>{label}</span>
    </>
  );
  const toggle = () => {
    setExpanded(!expanded);
    if (expanded) cancel();
    else load(uri);
  };

  return (
    <li className="ov-file-browser-search__scope-node">
      <div className="ov-file-browser-search__scope-row">
        <button
          type="button"
          className="ov-file-browser-search__scope-expand"
          aria-label={(expanded
            ? (text.tree?.collapse ?? treeText.collapse)
            : (text.tree?.expand ?? treeText.expand)
          ).replace('{name}', label)}
          aria-expanded={expanded}
          aria-controls={expanded ? id : undefined}
          onClick={toggle}
        >
          <ChevronIcon aria-hidden="true" />
        </button>
        {multiple ? (
          <Checkbox
            checked={checked}
            indeterminate={mixed}
            disabled={inherited}
            title={formatVikingUriLabel(uri)}
            onChange={(next) => onChange(uri, next)}
          >
            {content}
          </Checkbox>
        ) : (
          <Radio
            name={name}
            value={uri}
            checked={checked}
            title={formatVikingUriLabel(uri)}
            onChange={(next) => onChange(uri, next)}
          >
            {content}
          </Radio>
        )}
      </div>
      {expanded ? (
        <ul id={id} className="ov-file-browser-search__scope-branches">
          {page?.children.map((child) => (
            <FileBrowserSearchScopeTree
              key={child}
              uri={child}
              label={displayNameFromUri(child)}
              value={value}
              multiple={multiple}
              name={name}
              text={text}
              requestOptions={requestOptions}
              onChange={onChange}
            />
          ))}
          {page?.loading ? (
            <li className="ov-file-browser-search__scope-message" role="status">
              {text.loading}
            </li>
          ) : null}
          {page?.error || page?.hasMore ? (
            <li>
              <button
                type="button"
                className="ov-file-browser-search__scope-choice"
                onClick={() => load(uri, page)}
              >
                {page.error
                  ? text.retry
                  : (text.tree?.loadMore ?? treeText.loadMore)}
              </button>
            </li>
          ) : null}
          {page &&
          !page.loading &&
          !page.error &&
          !page.hasMore &&
          page.children.length === 0 ? (
            <li className="ov-file-browser-search__scope-message">
              {text.tree?.emptyDirectory ?? treeText.emptyDirectory}
            </li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}
