import type { HTMLAttributes, ReactNode } from 'react';

/**
 * 顶部的一个接入分组，例如「接入主流 Agent」「接入应用」「开发自己的 Agent」。
 * 分组名称与数量由宿主决定，组件只按顺序渲染页签。
 *
 * One tab of the view, for example "Mainstream agents", "Apps" or "Build your
 * own agent". The host decides how many groups exist and what they are called;
 * the component renders them in order.
 */
export interface AgentConnectTab {
  /**
   * 页签唯一标识。
   * Unique tab key.
   */
  id: string;
  /**
   * 页签显示文本。
   * Tab label.
   */
  label: string;
}

/**
 * 左侧列表中的一个条目，例如某个 Agent 或某种接入方式。
 *
 * One entry in the left list, for example an agent or an access method.
 */
export interface AgentConnectItem {
  /**
   * 条目唯一标识。
   * Unique item key.
   */
  id: string;
  /**
   * 条目名称。
   * Item name.
   */
  name: string;
  /**
   * 列表中的一句话说明。
   * One-line summary shown in the list.
   */
  description?: string;
  /**
   * 条目图标，可以是图片或图标节点。
   * Item icon, which can be an image or an icon node.
   */
  icon?: ReactNode;
}

/**
 * 右侧详情的正文。正文由宿主提供，组件只负责排版与复制，不解析业务含义。
 *
 * The detail body. The host supplies it; the component only lays it out and
 * copies it, without interpreting its meaning.
 */
export interface AgentConnectDetail {
  /**
   * 详情标题，通常与选中条目同名。
   * Detail title, usually the selected item's name.
   */
  title?: string;
  /**
   * 标题旁的图标。
   * Icon shown next to the title.
   */
  icon?: ReactNode;
  /**
   * 标题下方的说明文字。
   * Description under the title.
   */
  description?: string;
  /**
   * 标题下方的标签，例如「SDK」「境外」。
   * Tags under the title, for example "SDK" or "Overseas".
   */
  tags?: readonly string[];
  /**
   * 正文内容，可以是 Markdown 渲染后的节点。
   * Body content, which can be the rendered Markdown node.
   */
  content?: ReactNode;
  /**
   * 复制按钮要写入剪贴板的纯文本。组件负责写入并提示成功；不传时不渲染复制按钮。
   * Plain text the copy control writes to the clipboard. The component performs
   * the write and reports success; no copy control is rendered when omitted.
   */
  copyText?: string;
}

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface AgentConnectLabels {
  /**
   * 正文区域的标题，例如「完整指令」。
   * Heading of the body area, for example "Complete instructions".
   */
  instructions?: string;
  /**
   * 复制按钮文案。
   * Copy control label.
   */
  copy?: string;
  /**
   * 复制成功后的提示。
   * Message shown after a successful copy.
   */
  copied?: string;
  /**
   * 没有条目时的提示。
   * Message shown when there are no items.
   */
  empty?: string;
}

/**
 * Agent 接入视图的公开接口。
 *
 * 组件只渲染页签、左侧列表与右侧详情，并上报切换与复制意图；目录数据、正文
 * 内容、权限与跳转都由宿主提供。
 *
 * Public interface of the agent connection view.
 *
 * The component renders the tabs, the left list and the right detail, and
 * reports switch and copy intent. Catalog data, body content, permissions and
 * navigation all come from the host.
 */
export interface AgentConnectProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children' | 'onSelect' | 'onCopy'
> {
  /**
   * 顶部页签。
   * Tabs at the top.
   */
  tabs: readonly AgentConnectTab[];
  /**
   * 当前页签 id，由宿主持有。
   * Active tab id, owned by the host.
   */
  activeTabId: string;
  /**
   * 当前页签下的条目，按宿主给定顺序渲染。
   * Items of the active tab, rendered in the order the host gives.
   */
  items: readonly AgentConnectItem[];
  /**
   * 当前选中条目 id，由宿主持有。
   * Selected item id, owned by the host.
   */
  selectedId?: string;
  /**
   * 当前选中条目的详情。不传时右侧不渲染正文。
   * Detail of the selected item. No body is rendered when omitted.
   */
  detail?: AgentConnectDetail;
  /**
   * 切换页签时触发。
   * Invoked when a tab is selected.
   */
  onTabChange?: (tabId: string) => void;
  /**
   * 选中一个条目时触发。
   * Invoked when an item is selected.
   */
  onSelect?: (item: AgentConnectItem) => void;
  /**
   * 复制成功后触发，用于埋点或额外反馈。复制本身由组件完成，回调不承担写入。
   * 不传时仍渲染复制按钮，只是不通知。
   * Invoked after the body has been copied, for analytics or extra feedback.
   * The component performs the write; this callback never does. The copy
   * control still renders when omitted, it just reports nothing.
   */
  onCopy?: (text: string) => void;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: AgentConnectLabels;
}
