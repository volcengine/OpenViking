import type { OvRequestOptions } from '../../client/index.js';
import type { HTMLAttributes, ReactNode } from 'react';

/**
 * 面板模式。对话与命令共用同一面板，切换时各自保留状态。
 * Panel mode. Conversation and commands share one panel, each keeping its own
 * state when the mode switches.
 */
export type ConsoleMode = 'conversation' | 'command';

/** 交互面板模块的插槽。Slots of the console module. */
export interface ConsoleSlots {
  /**
   * 面板顶部工具栏。模式切换由模块渲染，这里放宿主专属入口。
   * Panel toolbar. The module renders the mode switch; put host-specific
   * entries here.
   */
  toolbar?: ReactNode;
  /**
   * 对话模式下输入区上方的附加内容，例如当前资源上下文。
   * Extra content above the composer in conversation mode, for example the
   * current resource context.
   */
  conversationContext?: ReactNode;
  /**
   * 命令模式下的命令帮助内容。省略时由模块按宿主声明的命令生成。
   * Command help in command mode. When omitted, the module derives it from the
   * commands the host declares.
   */
  commandHelp?: ReactNode;
}

/** 交互面板模块的可替换文案。Replaceable copy of the console module. */
export interface ConsoleLabels {
  /** 对话模式名称。Conversation mode label. */
  conversation?: string;
  /** 命令模式名称。Command mode label. */
  command?: string;
  /** 会话加载失败提示。Session load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
}

/**
 * 交互面板模块的属性。
 *
 * 模块自己通过 Hook 取数，props 里没有会话与命令记录：宿主给配置、受控模式、
 * 插槽与文案。打开资源、定位产物通过回调交给宿主，与文件浏览模块的联动不依赖
 * 对方的私有状态。
 *
 * Props of the console module.
 *
 * The module loads its own data through a Hook, so no prop carries sessions or
 * command records: the host supplies configuration, the controlled mode, slots
 * and copy. Opening a resource or locating an artifact goes back to the host
 * through a callback, and coordination with the file browser never reaches into
 * the other module's private state.
 */
export interface ConsoleProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onSelect' | 'children'
> {
  /**
   * 受控的当前模式。传入时由宿主决定模式，模块只上报切换意图。
   * The controlled mode. When given, the host owns the mode and the module only
   * reports switch intent.
   */
  mode?: ConsoleMode;
  /**
   * 非受控时的初始模式，缺省为 `conversation`。
   * Initial mode when uncontrolled; defaults to `conversation`.
   */
  defaultMode?: ConsoleMode;
  /**
   * 模式变化后触发。
   * Called after the mode changes.
   */
  onModeChange?: (mode: ConsoleMode) => void;
  /**
   * 资源成功打开后触发，参数为资源 URI；失败时不触发。
   * Called after a resource opens successfully, with its URI; not called on
   * failure.
   */
  onOpenUri?: (uri: string) => void;
  /**
   * 模块产生任务或资源变更时触发，宿主据此刷新目录、打开任务或定位产物。
   * Called when the module produces a task or a resource change, so the host can
   * refresh a directory, open a task or locate an artifact.
   */
  onResourceChange?: (uri: string) => void;
  /**
   * 插槽，见 {@link ConsoleSlots}。
   * Slots, see {@link ConsoleSlots}.
   */
  slots?: ConsoleSlots;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: ConsoleLabels;
  /**
   * 请求配置覆盖，逐字段覆盖最近的 Provider。
   * Request configuration override, applied field by field over the nearest
   * Provider.
   */
  requestOptions?: OvRequestOptions;
}
