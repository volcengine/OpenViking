import type { HTMLAttributes } from 'react';

/**
 * 文件浏览入口条的文案。两项的文案都可替换，缺省为英文。
 * Copy of the file browser entry row. Both labels are replaceable, English by
 * default.
 */
export interface FileBrowserEntriesLabels {
  /** 连接 Agent 的文案。Label of the connect-agent entry. */
  connectAgent?: string;
  /** 导入资源的文案。Label of the import-resources entry. */
  importResources?: string;
}

/**
 * 文件浏览入口条的属性。
 *
 * 它固定是两个入口：连接 Agent 与导入资源。宿主只决定显示哪几个、点击后做
 * 什么，图标、文案、等分排布与两者之间的分隔线都由组件负责——这两项在每个
 * 宿主下外观都相同，差别只在点击行为。
 *
 * 只显示一个入口时不画分隔线：线分隔的是两个相邻项，只剩一个就无从分隔。
 *
 * Props of the file browser entry row.
 *
 * It is fixed to two entries: connect an agent, import resources. The host only
 * decides which appear and what a click does; the icons, copy, equal-width layout
 * and the divider between them belong to the component, because both look the same
 * under every host and differ only in behaviour.
 *
 * With a single entry no divider is drawn: a divider separates two neighbours, and
 * there is nothing left to separate.
 */
export interface FileBrowserEntriesProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children'
> {
  /**
   * 连接 Agent 成功后触发。省略时不显示该入口。
   * Called after the connect-agent entry is clicked. Omit it to hide that entry.
   */
  onConnectAgent?: () => void;
  /**
   * 导入资源成功后触发。省略时不显示该入口。
   * Called after the import-resources entry is clicked. Omit it to hide that entry.
   */
  onImportResources?: () => void;
  /** 可替换文案，缺省为英文。Replaceable copy; English by default. */
  labels?: FileBrowserEntriesLabels;
}
