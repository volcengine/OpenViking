import type { SearchHit } from './search-types.js';
import SearchFileIcon from '../../../../icons/svg/file-browser/file-04.svg?react';
import { nameFromUri } from './fs-model.js';
import { directoryIcon } from './fs-icon.js';

/**
 * 用最近一级父目录区分同名文件，URI 保留根和末段。
 * Keep the nearest parent for same-name files and retain the URI root and leaf.
 */
function resultLocation(hit: SearchHit) {
  const name = nameFromUri(hit.uri);
  const path = hit.uri.startsWith('viking://')
    ? hit.uri.slice('viking://'.length).split('/').filter(Boolean)
    : [];
  const parent = path.length > 2 ? path.at(-2) : undefined;
  const label = parent ? `${parent}/${name}` : name;
  return {
    name: hit.line === undefined ? label : `${label}:${hit.line}`,
    uri: parent ? `viking://${path[0]}/.../${name}` : hit.uri,
  };
}

/**
 * 一条搜索结果：图标、文件名（grep 带行号）、右侧 URI、下一行摘要。
 *
 * 设计稿的结果行只有这三块，没有分数或层级指标位，因此这里也不渲染它们。
 * 整行是一个按钮——点一下就在树里定位并打开预览，与 studio 的
 * `revealResource` 一致。
 *
 * One search result: icon, file name (with the line number for grep), the URI on the
 * right, and the abstract on the next line.
 *
 * The design's row carries only these three, with no score or level metric, so none is
 * rendered. The whole row is a button: clicking locates it in the tree and opens the
 * preview, matching studio's `revealResource`.
 */
export interface FileBrowserSearchRowProps {
  hit: SearchHit;
  id: string;
  active: boolean;
  query: string;
  onHighlight: () => void;
  onOpen: (uri: string) => void;
}

export function FileBrowserSearchRow({
  hit,
  id,
  active,
  query,
  onHighlight,
  onOpen,
}: FileBrowserSearchRowProps) {
  const { name, uri } = resultLocation(hit);
  const index = query ? name.toLowerCase().indexOf(query.toLowerCase()) : -1;
  return (
    <li className="ov-file-browser-search__item" role="presentation">
      <button
        id={id}
        type="button"
        role="option"
        aria-selected={active}
        tabIndex={-1}
        onPointerEnter={(event) => {
          if (event.pointerType !== 'touch') onHighlight();
        }}
        onMouseDown={(event) => event.preventDefault()}
        className="ov-file-browser-search__result"
        onClick={() => onOpen(hit.uri)}
      >
        <span className="ov-file-browser-search__row-content">
          <span className="ov-file-browser-search__icon" aria-hidden="true">
            {hit.isDir ? directoryIcon() : <SearchFileIcon />}
          </span>
          <span className="ov-file-browser-search__body">
            <span className="ov-file-browser-search__line">
              <span className="ov-file-browser-search__name" title={name}>
                {index < 0 ? (
                  name
                ) : (
                  <>
                    {name.slice(0, index)}
                    <mark>{name.slice(index, index + query.length)}</mark>
                    {name.slice(index + query.length)}
                  </>
                )}
              </span>
              <span className="ov-file-browser-search__uri" title={hit.uri}>
                {uri}
              </span>
            </span>
            {hit.abstract ? (
              <span className="ov-file-browser-search__abstract">
                {hit.abstract}
              </span>
            ) : null}
          </span>
        </span>
      </button>
    </li>
  );
}
