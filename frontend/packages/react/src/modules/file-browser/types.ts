import type { OvRequestOptions } from '../../client/index.js';
import type { HTMLAttributes, ReactNode } from 'react';

/** 文件浏览模块的可替换文案。Replaceable copy of the file browser module. */
export interface FileBrowserLabels {
  /**
   * 占位说明。缺省为 `Placeholder. Functionality is not available yet.`。
   * Placeholder description. Defaults to "Placeholder. Functionality is not
   * available yet.".
   */
  description?: string;
  /** 区域标题。Area title. */
  title?: string;
  /** 搜索框占位文案。Search placeholder. */
  searchPlaceholder?: string;
  /** 搜索无结果提示。Message shown when a search returns nothing. */
  searchEmpty?: string;
  /** 刷新按钮的无障碍名称。Accessible name of the refresh control. */
  refresh?: string;
  /** 未选中资源时的提示。Message shown when no resource is selected. */
  empty?: string;
  /** 加载失败提示。Load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
}

/**
 * 工具栏按钮的属性。宿主在 `toolbarActions` 里用它组合自己的入口。
 * Props of a toolbar button. The host composes its own entries with it inside
 * `toolbarActions`.
 */
export interface FileBrowserToolbarButtonProps extends Omit<
  HTMLAttributes<HTMLButtonElement>,
  'children' | 'onClick'
> {
  /** 按钮图标。Button icon. */
  icon: ReactNode;
  /** 按钮文案。Button label. */
  children: ReactNode;
  /** 是否禁用，缺省为 `false`。Whether the button is disabled; defaults to `false`. */
  disabled?: boolean;
  /** 点击后触发。Called on click. */
  onClick?: () => void;
}

/**
 * 文件浏览模块的属性。
 *
 * 模块自己通过 Hook 取数，props 里没有数据字段：宿主给配置、受控的当前资源、
 * 工具栏入口、文案与请求配置。目录、搜索、预览页签与行级状态都在模块内维护，
 * 宿主不需要重复组装。
 *
 * 模块只覆盖文件树与文件预览两块。命令面板属于 `console`。
 *
 * Props of the file browser module.
 *
 * The module loads its own data through a Hook, so no prop carries data: the
 * host supplies configuration, the controlled current resource, the toolbar
 * entries, copy and request configuration. The directory, search, preview tabs
 * and per-row state all live inside the module, so the host does not assemble
 * them again.
 *
 * The module covers only the file tree and the file preview. The command panel
 * belongs to `console`.
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
   * 工具栏左侧的宿主入口，例如连接 Agent、导入资源。用
   * {@link FileBrowserToolbarButton} 组合，保证与模块内置按钮外观一致。
   * 搜索与刷新由模块渲染，入口之间的分隔线也由模块处理。
   *
   * Host entries on the left of the toolbar, for example connecting an agent or
   * importing resources. Compose them with {@link FileBrowserToolbarButton} so
   * they match the module's own buttons. Search and refresh are rendered by the
   * module, and so are the dividers between entries.
   */
  toolbarActions?: ReactNode;
  /**
   * 当前资源变化后触发，参数为新的 URI；清除选中时传入 `null`。
   * Called after the current resource changes, with the new URI; `null` when
   * the selection is cleared.
   */
  onUriChange?: (uri: string | null) => void;
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
