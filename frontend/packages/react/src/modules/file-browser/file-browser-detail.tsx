import type { Locale } from '../../shared/locale.js';
import {
  FilePreview,
  type FilePreviewActions,
  type FilePreviewTab,
} from '../../components/file-preview/index.js';
import type { ReactNode } from 'react';

import type { FileBrowserLabels } from './types.js';
import { CopyIcon, DownloadIcon } from './file-browser-detail-icons.js';

/**
 * 详情区：页签行与正文。分段器（预览 / 源码）与右侧图标按钮由这里渲染，通过
 * `slots.actions` 拿到预览的视图状态。
 *
 * 目录的摘要层级不在这里——它是 `directory` 页签内部的一层，由组件自己渲染。
 *
 * The detail pane: tab row and body. The segmented control (preview / source) and
 * the icon buttons on the right are rendered here, reading the view state from the
 * preview through `slots.actions`.
 *
 * A directory's summary levels are not here: they are a layer inside the
 * `directory` tab, rendered by the component itself.
 */
export interface FileBrowserDetailProps {
  tabs: readonly FilePreviewTab[];
  activeKey: string | null;
  locale: Locale;
  labels?: FileBrowserLabels['preview'];
  footer?: string;
  /** 分段器与图标按钮的文案。Copy for the segmented control and icon buttons. */
  viewLabels: {
    preview: string;
    source: string;
    copyPath: string;
    copyContent: string;
    download: string;
    viewSwitch: string;
  };
  onActiveKeyChange: (key: string) => void;
}

export function FileBrowserDetail({
  tabs,
  activeKey,
  locale,
  labels,
  footer,
  viewLabels,
  onActiveKeyChange,
}: FileBrowserDetailProps) {
  return (
    <div className="ov-file-browser__detail">
      <FilePreview
        tabs={
          decorateTabs(tabs, viewLabels, footer) as [
            FilePreviewTab,
            ...FilePreviewTab[],
          ]
        }
        activeKey={activeKey ?? undefined}
        locale={locale}
        labels={labels}
        onActiveKeyChange={onActiveKeyChange}
      />
    </div>
  );
}

/**
 * 把标题行动作插槽挂到每个页签上——`slots` 属于页签，不属于预览组件。
 *
 * 按钮由组件的动作上下文决定，模块只负责排版：能切视图就出分段器，能复制正文
 * 就出复制按钮，能下载就出下载按钮。目录与文件的差别因此自动落在能力上——目录
 * 的正文是生成出来的摘要，可以复制但没有下载；文件则按格式与操作决定。
 *
 * Attaches the header action slot to every tab: `slots` belongs to a tab, not to
 * the preview component.
 *
 * The buttons follow the component's action context and the module only lays them
 * out: a segmented control when the view can switch, a copy button when there is
 * copyable text, a download button when downloading is available. The directory /
 * file difference therefore falls out of the capabilities — a directory's body is
 * a generated summary, copyable but not downloadable; a file depends on its format
 * and operations.
 */
function decorateTabs(
  tabs: readonly FilePreviewTab[],
  viewLabels: FileBrowserDetailProps['viewLabels'],
  footer: ReactNode,
): readonly FilePreviewTab[] {
  return tabs.map((tab) => {
    if (tab.kind === 'content') return tab;
    return {
      ...tab,
      slots: {
        ...tab.slots,
        // TODO(file-browser): 底部信息现在由模块算好后作为节点透传，组件只负责
        // 摆放。后续改成组件内置——字符数、更新时间这些是每个宿主都要的，让模块
        // 自己拼字符串既漏了 i18n，也把同一段逻辑复制到每个宿主。
        //
        // TODO(file-browser): the footer is computed by the module and passed down as
        // a node, with the component only placing it. It should become built in:
        // character count and update time are wanted by every host, and having the
        // module build the string both skips i18n and duplicates the logic per host.
        footer,
        actions: (actions: FilePreviewActions) => (
          <div className="ov-file-browser__detail-actions">
            {actions.canSwitchView ? (
              <div
                className="ov-file-browser__segmented"
                role="group"
                aria-label={viewLabels.viewSwitch}
              >
                <button
                  type="button"
                  className="ov-file-browser__segment"
                  data-active={actions.viewMode === 'preview' || undefined}
                  onClick={() => actions.setViewMode('preview')}
                >
                  {viewLabels.preview}
                </button>
                <button
                  type="button"
                  className="ov-file-browser__segment"
                  data-active={actions.viewMode === 'source' || undefined}
                  onClick={() => actions.setViewMode('source')}
                >
                  {viewLabels.source}
                </button>
              </div>
            ) : null}
            {actions.canCopyContent || actions.canDownload ? (
              <span
                aria-hidden="true"
                className="ov-file-browser__detail-sep"
              />
            ) : null}
            {actions.canCopyContent ? (
              <button
                type="button"
                className="ov-file-browser__detail-icon"
                aria-label={viewLabels.copyContent}
                title={viewLabels.copyContent}
                onClick={() => void actions.copyContent()}
              >
                <CopyIcon />
              </button>
            ) : null}
            {actions.canDownload ? (
              <button
                type="button"
                className="ov-file-browser__detail-icon"
                aria-label={viewLabels.download}
                title={viewLabels.download}
                onClick={() => actions.download()}
              >
                <DownloadIcon />
              </button>
            ) : null}
          </div>
        ),
      },
    };
  });
}
