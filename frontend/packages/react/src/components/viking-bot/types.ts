import type { ErrorFallback } from '../../shared/error-boundary/types.js';
import type { Locale } from '../../shared/locale.js';
import type { HTMLAttributes, ReactNode, RefObject } from 'react';

import type { VikingBotInteractionProps } from './interaction/types.js';
import type {
  VikingBotInputFeatures,
  VikingBotSessions,
} from './capabilities.js';
import type {
  VikingBotAttachment,
  VikingBotDraft,
  VikingBotLabels,
  VikingBotMessage,
  VikingBotStreamingState,
} from './model.js';

/** 消息列表的公开接口。Public interface of the message list. */
export interface VikingBotMessageListProps {
  /** 当前会话的消息。Messages in the current session. */
  messages: readonly VikingBotMessage[];
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /** 附加到根元素的类名。Class name applied to the root element. */
  className?: string;
  /** 助手仍在生成时追加的片段。Parts appended while the assistant is still streaming. */
  streaming?: VikingBotStreamingState;
  /** 渲染在最后一条消息之后，例如记忆提交提示条。Rendered after the last message. */
  footer?: ReactNode;
  /** 当前身份作用域的标识；值变化时重置展示状态。Identity scope; changes reset display state. */
  listKey?: string;
  /** 打开消息中引用的资源。Opens a resource referenced by a message. */
  onOpenUri?: (uri: string) => void;
  /** 复制由宿主执行，列表只上报意图。The host copies; the list reports intent. */
  onCopy?: (text: string) => void;
  /** 触发一条消息声明的动作，例如提交记忆。Fires a message action. */
  onMessageAction?: (messageId: string, actionKey: string) => void;
  /** 消息正文的替换渲染，用于宿主自带 Markdown 渲染器。Replaces message bodies. */
  renderMessage?: (message: VikingBotMessage) => ReactNode;
  /** 渲染异常时的替代内容，省略时继承外层或使用内置展示。Render-failure content; inherits or uses the default when omitted. */
  slots?: { errorFallback?: ErrorFallback };
  /** 可替换文案，未提供的项使用当前 locale 的内置文案。Replaceable copy; omitted fields follow locale. */
  labels?: VikingBotLabels;
}

/** 输入区的命令式入口。Imperative handle of the composer. */
export interface VikingBotComposerRef {
  /** 替换输入文本。Replaces the input text. */
  setText: (text: string) => void;
  /** 追加文本，保留已编辑的草稿。Appends text, keeping the current draft. */
  appendText: (text: string) => void;
  /** 添加在输入区外拖入的文件。Adds files dropped outside the input area. */
  addFiles: (files: readonly File[]) => void;
  /** 是否已达到附件数量上限。Whether the attachment limit has been reached. */
  isAttachmentLimitReached: () => boolean;
  /** 清空输入文本；附件与引用由宿主重置。Clears text; the host resets attachments and references. */
  clear: () => void;
  /** 聚焦输入框。Focuses the input. */
  focus: () => void;
}

/** 输入区的公开接口。Public interface of the composer. */
export interface VikingBotComposerProps extends VikingBotInputFeatures {
  /** 受控的草稿文本。Controlled draft text. */
  value: string;
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /** 附加到根元素的类名。Class name applied to the root element. */
  className?: string;
  /** 宿主已选中的附件。Attachments already selected by the host. */
  attachments?: readonly VikingBotAttachment[];
  /** 是否禁用发送；原因与提示由宿主决定。Disables sending; the host owns the reason. */
  disabled?: boolean;
  /** 禁用时展示的说明。Reason shown while the composer is disabled. */
  disabledReason?: string;
  /** 是否正在回复；回复中停止动作替代发送。Whether a reply is streaming. */
  isSending?: boolean;
  /** Allow sending additional input during a reply only when the host supports it. */
  canSendWhileStreaming?: boolean;
  /** Awaiting input acceptance; disables duplicate submissions, not editing. */
  isSubmitting?: boolean;
  /** 输入区布局形态。Layout of the input area. */
  variant?: 'default' | 'compact';
  /** 宿主接受的 MIME 类型。MIME types the host accepts. */
  accept?: string;
  /** 宿主允许的最大附件数。Maximum number of attachments the host allows. */
  maxAttachments?: number;
  /** 发送按钮旁的额外控件。Extra controls rendered next to the send button. */
  actions?: ReactNode;
  /** 输入值变化时触发。省略时输入框只读。Invoked when the input changes. */
  onValueChange?: (value: string) => void;
  /** 上报用户选择或拖入的文件；由宿主上传。Reports files the user picked or dropped. */
  onFilesAdded?: (files: readonly File[]) => void;
  /** 移除一个附件。Removes an attachment. */
  onAttachmentRemove?: (attachment: VikingBotAttachment) => void;
  /** 打开已选中的文件或目录引用。Opens a selected file or directory reference. */
  onOpenUri?: (uri: string) => void;
  /** 上报发送意图；由宿主发送并清空草稿。省略时不能发送。Reports the send intent. */
  onSend?: (draft: VikingBotDraft) => void;
  /** 停止正在进行的回复。省略时不展示停止动作。Stops the streaming reply. */
  onCancel?: () => void;
  /** 渲染异常时的替代内容，省略时继承外层或使用内置展示。Render-failure content; inherits or uses the default when omitted. */
  slots?: { errorFallback?: ErrorFallback };
  /** 可替换文案，未提供的项使用当前 locale 的内置文案。Replaceable copy; omitted fields follow locale. */
  labels?: VikingBotLabels;
}

/** 完整对话区的公开接口。Public interface of the whole conversation view. */
export interface VikingBotProps
  extends
    VikingBotInputFeatures,
    Omit<HTMLAttributes<HTMLDivElement>, 'onCopy' | 'onSubmit'> {
  /** 当前会话的消息。Messages in the current session. */
  messages: readonly VikingBotMessage[];
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /** Host-owned session history and actions. */
  sessions?: VikingBotSessions;
  /** Optional extension controls, such as a host-provided More menu. */
  headerActions?: ReactNode;
  /** 附加到根元素的类名。Class name applied to the root element. */
  className?: string;
  /** 助手仍在生成时追加的片段。Parts appended while the assistant is still streaming. */
  streaming?: VikingBotStreamingState;
  /** 受控的草稿文本；不传则不展示输入区。Controlled draft text; omit to hide the composer. */
  draft?: string;
  /** 宿主已选中的附件。Attachments already selected by the host. */
  attachments?: readonly VikingBotAttachment[];
  /** 是否正在回复。Whether a reply is streaming. */
  isSending?: boolean;
  /** Supplied from the service's canSteer capability. Defaults to false. */
  canSendWhileStreaming?: boolean;
  isSubmitting?: boolean;
  /** 是否可接受输入。Whether the bot can accept input. */
  isAvailable?: boolean;
  /** 不可用时的说明。Message shown while the bot is unavailable. */
  unavailableMessage?: string;
  /** 首条消息前的内容；undefined 使用欢迎页，null 隐藏。Empty content: undefined uses welcome; null hides it. */
  emptyState?: ReactNode;
  /** 头部插槽，例如会话标题或历史按钮。Header slot. */
  header?: ReactNode;
  /** 渲染在输入区上方，例如记忆提交提示条。Rendered above the composer. */
  footer?: ReactNode;
  /** 替换整个主体，用于宿主自定义组合。Replaces the whole body. */
  children?: ReactNode;
  /** 替换消息列表，优先于空态；null 隐藏。Replaces the list, including empty state; null hides it. */
  messageList?: ReactNode;
  /** 替换输入区，用于宿主自带输入组件。Replaces the composer. */
  composer?: ReactNode;
  /**
   * 待确认交互的配置；存在时替代输入区。
   * Configuration of a pending interaction; replaces the composer.
   */
  interaction?: VikingBotInteractionProps;
  /**
   * 整块替换交互区；传值时忽略 `interaction` 的内置渲染，宿主自行渲染。
   * Replaces the whole interaction area; when set, the built-in rendering of
   * `interaction` is skipped and the host renders its own.
   */
  interactionSlot?: ReactNode;
  /** 当前身份作用域的标识；值变化时重置展示状态。Identity scope; changes reset display state. */
  listKey?: string;
  /** 输入区的命令式入口。Imperative handle of the composer. */
  composerRef?: RefObject<VikingBotComposerRef | null>;
  /** 草稿变化时触发。Invoked when the draft changes. */
  onDraftChange?: (value: string) => void;
  /** 上报用户选择或拖入的文件；由宿主上传。Reports files the user picked or dropped. */
  onFilesAdded?: (files: readonly File[]) => void;
  /** 移除一个附件。Removes an attachment. */
  onAttachmentRemove?: (attachment: VikingBotAttachment) => void;
  /** 上报发送意图。Reports the send intent. */
  onSend?: (draft: VikingBotDraft) => void;
  /** 停止正在进行的回复。Stops the streaming reply. */
  onCancel?: () => void;
  /** 打开消息资源或输入区引用。Opens a message resource or composer reference. */
  onOpenUri?: (uri: string) => void;
  /** 复制由宿主执行，组件只上报意图。The host copies; the component reports intent. */
  onCopy?: (text: string) => void;
  /** 触发一条消息声明的动作。Fires a message action. */
  onMessageAction?: (messageId: string, actionKey: string) => void;
  /** 渲染异常时的替代内容，省略时继承外层或使用内置展示。Render-failure content; inherits or uses the default when omitted. */
  slots?: { errorFallback?: ErrorFallback };
  /** 可替换文案，未提供的项使用当前 locale 的内置文案。Replaceable copy; omitted fields follow locale. */
  labels?: VikingBotLabels;
}
