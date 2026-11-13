import type { SearchHit } from './search-types.js';
import { nameFromUri } from './fs-model.js';
import { fileTypeIcon } from './fs-icon.js';

/**
 * 结果行的显示名：文件名，grep 命中再带上行号。
 *
 * 文件名复用 `fs-model` 的 `nameFromUri`，与树行、预览页签取同一份规则——各写
 * 一份在 `viking://` 这类边界上会给出不同结果。
 *
 * Display name of a result row: the file name, with a grep hit's line number appended.
 *
 * The name comes from `fs-model`'s `nameFromUri`, the same rule the tree row and the
 * preview tab use; separate copies disagree at boundaries such as `viking://`.
 */
function hitName(hit: SearchHit): string {
  const name = nameFromUri(hit.uri);
  return hit.line === undefined ? name : `${name}:${hit.line}`;
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
  onHover: () => void;
  onLeave: () => void;
  onOpen: (uri: string) => void;
}

export function FileBrowserSearchRow({
  hit,
  id,
  active,
  query,
  onHover,
  onLeave,
  onOpen,
}: FileBrowserSearchRowProps) {
  const name = hitName(hit);
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
          if (event.pointerType !== 'touch') onHover();
        }}
        onPointerLeave={onLeave}
        onMouseDown={(event) => event.preventDefault()}
        className="ov-file-browser-search__result"
        onClick={() => onOpen(hit.uri)}
      >
        <span className="ov-file-browser-search__icon" aria-hidden="true">
          {fileTypeIcon(nameFromUri(hit.uri))}
        </span>
        <span className="ov-file-browser-search__body">
          <span className="ov-file-browser-search__line">
            <span className="ov-file-browser-search__name">
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
            <span className="ov-file-browser-search__uri">{hit.uri}</span>
          </span>
          {hit.abstract ? (
            <span className="ov-file-browser-search__abstract">
              {hit.abstract}
            </span>
          ) : null}
        </span>
      </button>
    </li>
  );
}
