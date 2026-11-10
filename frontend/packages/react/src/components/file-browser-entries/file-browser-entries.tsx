import { useLocale } from '../../shared/locale-context.js';
import { useId, useRef, useState } from 'react';
import { resolveFileBrowserEntriesLabels } from './labels.js';

import { Divider } from '../../shared/components/divider/index.js';
import { Drawer } from '../../shared/components/drawer/index.js';
import { AgentConnect } from '../agent-connect/index.js';
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
 * 传入 agentConnect 时内置右侧接入抽屉，回调仍可单独用于宿主自定义接入。
 * 没有可见入口时不渲染入口条，导入入口支持宿主包装默认按钮。
 *
 * The file browser entry row: connect an agent, import resources.
 *
 * agentConnect enables the right-side drawer; callbacks also support host-owned flows.
 * Nothing renders without visible entries; hosts can wrap the default import button.
 */
export function FileBrowserEntries({
  locale: localeProp,
  agentConnect,
  onConnectAgent,
  onImportResources,
  renderImportResources,
  labels,
  className,
  ...rest
}: FileBrowserEntriesProps) {
  const locale = useLocale(localeProp);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const drawerId = useId();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const text = resolveFileBrowserEntriesLabels(locale, labels);
  const showConnect = Boolean(agentConnect || onConnectAgent);
  const importButton = (
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
  );
  const importEntry = renderImportResources
    ? renderImportResources(importButton)
    : onImportResources
      ? importButton
      : null;
  const showImport = importEntry != null && typeof importEntry !== 'boolean';
  if (!showConnect && !showImport) return null;

  return (
    <div
      {...rest}
      className={['ov-file-browser-entries', className]
        .filter(Boolean)
        .join(' ')}
    >
      {showConnect ? (
        <button
          ref={triggerRef}
          type="button"
          className="ov-file-browser-entries__entry"
          aria-haspopup={agentConnect ? 'dialog' : undefined}
          aria-controls={agentConnect ? drawerId : undefined}
          aria-expanded={agentConnect ? drawerOpen : undefined}
          onClick={() => {
            if (agentConnect) setDrawerOpen(true);
            onConnectAgent?.();
          }}
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
      {showConnect && showImport ? (
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
          color="var(--ov-ui-color-primary, var(--ov-ui-primary-default))"
          style={{ opacity: 0.5 }}
        />
      ) : null}
      {agentConnect ? (
        <Drawer
          id={drawerId}
          open={drawerOpen}
          title={text.connectAgent}
          closeLabel={text.close}
          triggerRef={triggerRef}
          className="ov-file-browser-entries__drawer"
          lang={agentConnect.locale ?? locale}
          onClose={() => setDrawerOpen(false)}
        >
          <AgentConnect
            {...agentConnect}
            locale={agentConnect.locale ?? locale}
          />
        </Drawer>
      ) : null}
      {showImport ? (
        <span className="ov-file-browser-entries__import-slot">
          {importEntry}
        </span>
      ) : null}
    </div>
  );
}
