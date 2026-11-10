import type { Locale } from '../../shared/locale.js';
import type {
  ComponentPropsWithRef,
  HTMLAttributes,
  ReactElement,
  ReactNode,
} from 'react';
import type { AgentConnectProps } from '../agent-connect/index.js';

/**
 * 文件浏览入口条与抽屉的文案。未覆盖项跟随 locale。
 * Copy of the file browser entries and drawer. Omitted fields follow locale.
 */
export interface FileBrowserEntriesLabels {
  /** 连接 Agent 的文案。Label of the connect-agent entry. */
  connectAgent?: string;
  /** 导入资源的文案。Label of the import-resources entry. */
  importResources?: string;
  /** 右侧接入抽屉的关闭文案。Close label of the connection drawer. */
  close?: string;
}

/**
 * 文件浏览入口条的属性。
 *
 * 固定两个入口：连接 Agent 与导入资源。agentConnect 可启用内置右侧接入抽屉，
 * 也可仅通过回调接入宿主流程。宿主可包装或替换默认导入按钮。
 * 图标、文案、等分排布与分隔线由组件负责。
 *
 * 只显示一个入口时不画分隔线：线分隔的是两个相邻项，只剩一个就无从分隔。
 *
 * Props of the file browser entry row.
 *
 * Two fixed entries: connect an agent and import resources. agentConnect enables
 * the built-in connection drawer; callbacks also support host-owned flows.
 * Hosts can wrap or replace the default import button. The component owns the
 * icons, copy, equal-width layout and divider.
 *
 * With a single entry no divider is drawn: a divider separates two neighbours, and
 * there is nothing left to separate.
 */
export interface FileBrowserEntriesProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children'
> {
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /**
   * 内置 AgentConnect 右侧抽屉的参数；传入后显示连接入口。
   * Options for the built-in AgentConnect drawer; enables the connect entry.
   */
  agentConnect?: AgentConnectProps;
  /**
   * 点击连接入口时通知；未传 agentConnect 时由宿主处理接入。
   * Click notification; the host owns connection when agentConnect is omitted.
   */
  onConnectAgent?: () => void;
  /**
   * 默认导入按钮被点击时触发；未提供导入回调和渲染插槽时隐藏入口。
   * Called when the default import button is clicked; omit both import props to hide it.
   */
  onImportResources?: () => void;
  /**
   * 自定义导入入口；参数是带有图标、文案和点击回调的默认原生按钮。
   * 传入后显示入口，返回 null 时隐藏；不传时按 onImportResources 渲染默认按钮。
   * Renders the import entry using its default native button, including icon, label
   * and click handler. Enables the entry; return null to hide it. When omitted,
   * onImportResources enables the default button.
   */
  renderImportResources?: (
    trigger: ReactElement<ComponentPropsWithRef<'button'>>,
  ) => ReactNode;
  /** 可替换文案，未提供的项使用当前 locale 的内置文案。Replaceable copy; omitted fields follow locale. */
  labels?: FileBrowserEntriesLabels;
}
