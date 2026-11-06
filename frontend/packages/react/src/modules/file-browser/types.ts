import type { OvRequestOptions } from '../../client/index.js';
import type { HTMLAttributes, ReactNode } from 'react';

/** 文件浏览模块的插槽。Slots of the file browser module. */
export interface FileBrowserSlots {
  /**
   * 目录区工具栏。搜索与刷新由模块自己提供，这里放宿主专属入口，例如接入
   * Agent、导入资源。
   * Toolbar of the directory area. Search and refresh come from the module; put
   * host-specific entries here, for example connecting an agent or importing
   * resources.
   */
  toolbar?: ReactNode;
  /**
   * 预览区额外的页签。经验看板、会话内容等有独立取数逻辑的视图由宿主提供节点，
   * 模块不识别它们的业务类型。
   * Extra preview tabs. Views with their own loading logic, such as experience
   * dashboards or session content, are supplied by the host; the module does not
   * recognise their business types.
   */
  previewTabs?: ReactNode;
  /**
   * 配额提示等产品内容。
   * Product content such as quota hints.
   */
  quotaHint?: ReactNode;
}

/** 文件浏览模块的可替换文案。Replaceable copy of the file browser module. */
export interface FileBrowserLabels {
  /** 区域标题。Area title. */
  title?: string;
  /** 搜索框占位文案。Search placeholder. */
  searchPlaceholder?: string;
  /** 搜索无结果提示。Message shown when a search returns nothing. */
  searchEmpty?: string;
  /** 未选中资源时的提示。Message shown when no resource is selected. */
  empty?: string;
  /** 加载失败提示。Load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
}

/**
 * 文件浏览模块的属性。
 *
 * 模块自己通过 Hook 取数，props 里没有数据字段：宿主给配置、受控的当前资源、
 * 插槽与文案，模块通过回调上报资源变化。目录、搜索与预览的联动在模块内维护，
 * 宿主无需重复组装。
 *
 * Props of the file browser module.
 *
 * The module loads its own data through a Hook, so no prop carries data: the
 * host supplies configuration, the controlled current resource, slots and copy,
 * and the module reports resource changes through a callback. Coordination
 * between directory, search and preview stays inside the module, so the host
 * does not assemble it again.
 */
export interface FileBrowserProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onSelect' | 'children'
> {
  /**
   * 受控的当前资源 URI。传入时由宿主决定当前资源，模块只上报切换意图。
   * The controlled current resource URI. When given, the host owns the current
   * resource and the module only reports switch intent.
   */
  uri?: string;
  /**
   * 非受控时的初始资源 URI。省略时不选中任何资源。
   * Initial resource URI when uncontrolled. Nothing is selected when omitted.
   */
  defaultUri?: string;
  /**
   * 当前资源变化后触发，参数为新的 URI；清除选中时传入 `null`。
   * Called after the current resource changes, with the new URI; `null` when
   * the selection is cleared.
   */
  onUriChange?: (uri: string | null) => void;
  /**
   * 插槽，见 {@link FileBrowserSlots}。
   * Slots, see {@link FileBrowserSlots}.
   */
  slots?: FileBrowserSlots;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: FileBrowserLabels;
  /**
   * 请求配置覆盖，逐字段覆盖最近的 Provider。
   * Request configuration override, applied field by field over the nearest
   * Provider.
   */
  requestOptions?: OvRequestOptions;
}
