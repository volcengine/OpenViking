import { resolveFileBrowserEntriesLabels } from './labels.js';
import { Divider } from '../../shared/components/divider/index.js';
import {
  ConnectAgentIcon,
  ImportResourcesIcon,
} from './file-browser-entries-icons.js';
import type { FileBrowserEntriesProps } from './types.js';

export type {
  FileBrowserEntriesLabels,
  FileBrowserEntriesProps,
} from './types.js';

/**
 * 文件浏览入口条：连接 Agent 与导入资源。
 *
 * 两项都省略回调时不渲染任何东西，入口条因此整块消失——宿主既没接连接也没接
 * 导入时，留一条空白底色只会让人以为还有内容没加载出来。
 *
 * The file browser entry row: connect an agent, import resources.
 *
 * With both callbacks omitted nothing renders and the row disappears entirely —
 * leaving an empty tinted strip would suggest something failed to load.
 */
export function FileBrowserEntries({
  locale = 'en-US',
  onConnectAgent,
  onImportResources,
  labels,
  className,
  ...rest
}: FileBrowserEntriesProps) {
  const text = resolveFileBrowserEntriesLabels(locale, labels);
  if (!onConnectAgent && !onImportResources) return null;

  return (
    <div
      {...rest}
      className={['ov-file-browser-entries', className]
        .filter(Boolean)
        .join(' ')}
    >
      {onConnectAgent ? (
        <button
          type="button"
          className="ov-file-browser-entries__entry"
          onClick={onConnectAgent}
        >
          <span className="ov-file-browser-entries__icon" aria-hidden="true">
            <ConnectAgentIcon />
          </span>
          <span className="ov-file-browser-entries__label">
            {text.connectAgent}
          </span>
        </button>
      ) : null}
      {/* 分隔线只在两个入口都在时出现：一个入口没有相邻项可分隔。 */}
      {/* The divider appears only when both entries are present: a single entry has
          no neighbour to separate from. */}
      {onConnectAgent && onImportResources ? (
        <Divider
          className="ov-file-browser-entries__divider"
          length={12}
          // 入口条是主色浅底，分隔线取主色并压到半透明，才不像一道灰线；
          // 颜色走 prop 而不是 CSS 覆盖——组件把颜色写在行内，类选择器盖不过。
          //
          // The row sits on a primary tint, so the divider takes the primary colour
          // at half opacity instead of reading as a grey line. The colour goes
          // through the prop rather than a CSS override: the component writes it
          // inline, which a class selector cannot beat.
          color="var(--ov-ui-color-primary, var(--ov-ui-primary-default, #5252ff))"
          style={{ opacity: 0.5 }}
        />
      ) : null}
      {onImportResources ? (
        <button
          type="button"
          className="ov-file-browser-entries__entry"
          onClick={onImportResources}
        >
          <span className="ov-file-browser-entries__icon" aria-hidden="true">
            <ImportResourcesIcon />
          </span>
          <span className="ov-file-browser-entries__label">
            {text.importResources}
          </span>
        </button>
      ) : null}
    </div>
  );
}
