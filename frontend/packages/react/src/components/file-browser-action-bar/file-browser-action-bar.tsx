import { useTrack } from '../../shared/analytics/use-track.js';
import {
  useExposure,
  useOpenExposure,
} from '../../shared/analytics/use-exposure.js';
import { Button } from '../../shared/primitives/button/button.js';
import { useLocale } from '../../shared/locale-context.js';
import { useId, useRef, useState } from 'react';
import { ImportResourcesButton } from './import-resources-button.js';
import { resolveFileBrowserActionBarLabels } from './labels.js';

import { Divider } from '../../shared/primitives/divider/index.js';
import { Drawer } from '../../shared/primitives/drawer/index.js';
import { AgentConnect } from '../agent-connect/index.js';
import {
  ConnectAgentIcon,
  ImportResourcesIcon,
} from './file-browser-action-bar-icons.js';
import type { FileBrowserActionBarProps } from './types.js';

export type {
  FileBrowserActionBarLabels,
  FileBrowserActionBarProps,
} from './types.js';

/**
 * 文件浏览操作栏：连接 Agent 与导入资源。
 *
 * 传入 agentConnect 时内置右侧接入抽屉，回调仍可单独用于宿主自定义接入。
 * 没有可见操作时不渲染操作栏，导入操作支持宿主包装默认按钮。
 *
 * The file browser action bar: connect an agent, import resources.
 *
 * agentConnect enables the right-side drawer; callbacks also support host-owned flows.
 * Nothing renders without visible actions; hosts can wrap the default import button.
 */
export function FileBrowserActionBar({
  locale: localeProp,
  agentConnect,
  agentConnectOpen,
  onAgentConnectOpenChange,
  onConnectAgent,
  onImportResources,
  renderImportResources,
  labels,
  className,
  ...rest
}: FileBrowserActionBarProps) {
  const track = useTrack();
  const locale = useLocale(localeProp);
  const connectExposure = useExposure('file_browser_connect_agent_show');
  const triggerRef = useRef<HTMLButtonElement>(null);
  const drawerId = useId();
  const [internalDrawerOpen, setInternalDrawerOpen] = useState(false);
  const drawerOpen = agentConnectOpen ?? internalDrawerOpen;
  const drawerExposure = useOpenExposure(
    'file_browser_agent_connect_drawer_show',
    drawerOpen,
  );
  const requestDrawerOpen = (open: boolean) => {
    if (open === drawerOpen) return;
    if (agentConnectOpen === undefined) setInternalDrawerOpen(open);
    onAgentConnectOpenChange?.(open);
  };
  const text = resolveFileBrowserActionBarLabels(locale, labels);
  const showConnect = Boolean(agentConnect || onConnectAgent);
  const importButton = (
    <ImportResourcesButton
      htmlType="button"
      className="ov-file-browser-action-bar__action"
      onClick={() => {
        track('file_browser_import_resources_click');
        onImportResources?.();
      }}
    >
      <span className="ov-file-browser-action-bar__icon" aria-hidden="true">
        <ImportResourcesIcon />
      </span>
      <span className="ov-file-browser-action-bar__label">
        {text.importResources}
      </span>
    </ImportResourcesButton>
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
      className={['ov-file-browser-action-bar', className]
        .filter(Boolean)
        .join(' ')}
    >
      {showConnect ? (
        <Button
          ref={(node) => {
            triggerRef.current = node;
            connectExposure?.(node);
          }}
          htmlType="button"
          className="ov-file-browser-action-bar__action"
          aria-haspopup={agentConnect ? 'dialog' : undefined}
          aria-controls={agentConnect ? drawerId : undefined}
          aria-expanded={agentConnect ? drawerOpen : undefined}
          onClick={() => {
            track('file_browser_connect_agent_click');
            if (agentConnect) requestDrawerOpen(true);
            onConnectAgent?.();
          }}
        >
          <span className="ov-file-browser-action-bar__icon" aria-hidden="true">
            <ConnectAgentIcon />
          </span>
          <span className="ov-file-browser-action-bar__label">
            {text.connectAgent}
          </span>
        </Button>
      ) : null}
      {/* 分隔线只在两个操作都在时出现：一个操作没有相邻项可分隔。 */}
      {/* The divider appears only when both entries are present: a single entry has
          no neighbour to separate from. */}
      {showConnect && showImport ? (
        <Divider
          className="ov-file-browser-action-bar__divider"
          length={12}
          // 操作栏是主色浅底，分隔线取主色并压到半透明，才不像一道灰线；
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
          className="ov-file-browser-action-bar__drawer"
          lang={agentConnect.locale ?? locale}
          onClose={() => requestDrawerOpen(false)}
        >
          <div ref={drawerExposure}>
            <AgentConnect
              {...agentConnect}
              locale={agentConnect.locale ?? locale}
            />
          </div>
        </Drawer>
      ) : null}
      {showImport ? (
        <span className="ov-file-browser-action-bar__import-slot">
          {importEntry}
        </span>
      ) : null}
    </div>
  );
}
