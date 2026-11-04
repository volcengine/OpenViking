import type { HTMLAttributes, ReactNode, RefObject } from 'react';

/** 纯文本片段。A plain text part. */
export interface VikingBotTextPart {
  /**
   * 片段类型，固定为 `text`。
   * Part type discriminator, always `text`.
   */
  type: 'text';
  /**
   * 文本内容。
   * Text content.
   */
  text: string;
}

/** 推理过程片段。A reasoning part. */
export interface VikingBotReasoningPart {
  /**
   * 片段类型，固定为 `reasoning`。
   * Part type discriminator, always `reasoning`.
   */
  type: 'reasoning';
  /**
   * 推理文本。
   * Reasoning text.
   */
  text: string;
  /**
   * 是否仍在生成。
   * Whether the reasoning is still being generated.
   */
  running?: boolean;
}

/** Agent 循环的迭代标记。An agent-loop iteration marker. */
export interface VikingBotIterationPart {
  /**
   * 片段类型，固定为 `iteration`。
   * Part type discriminator, always `iteration`.
   */
  type: 'iteration';
  /**
   * 迭代轮次。
   * Iteration number.
   */
  iteration: number;
}

/** 上下文引用片段，例如记忆、资源或技能。A context reference part. */
export interface VikingBotContextPart {
  /**
   * 片段类型，固定为 `context`。
   * Part type discriminator, always `context`.
   */
  type: 'context';
  /**
   * 被引用的资源 URI。
   * URI of the referenced resource.
   */
  uri: string;
  /**
   * 引用类别。
   * Reference category.
   */
  contextType:
    /**
     * 记忆。
     * Memory.
     */
    | 'memory'
    /**
     * 资源。
     * Resource.
     */
    | 'resource'
    /**
     * 技能。
     * Skill.
     */
    | 'skill';
  /**
   * 引用显示名称，缺省时由 URI 推导。
   * Display label; derived from the URI when omitted.
   */
  label?: string;
  /**
   * 引用摘要。
   * Reference abstract.
   */
  abstract?: string;
}

/** 工具调用片段。A tool call part. */
export interface VikingBotToolPart {
  /**
   * 片段类型，固定为 `tool`。
   * Part type discriminator, always `tool`.
   */
  type: 'tool';
  /**
   * 工具调用唯一标识。
   * Unique tool call key.
   */
  id: string;
  /**
   * 工具名称。
   * Tool name.
   */
  name: string;
  /**
   * 调用状态。
   * Call status.
   */
  status:
    /**
     * 待执行。
     * Waiting to run.
     */
    | 'pending'
    /**
     * 执行中。
     * Running.
     */
    | 'running'
    /**
     * 执行成功。
     * Finished successfully.
     */
    | 'completed'
    /**
     * 执行失败。
     * Finished with an error.
     */
    | 'error';
  /**
   * 调用入参。
   * Call input.
   */
  input?: Record<string, unknown>;
  /**
   * 调用输出文本。
   * Call output text.
   */
  output?: string;
  /**
   * 调用耗时，单位毫秒。
   * Call duration in milliseconds.
   */
  durationMs?: number;
}

/** 附件片段。An attachment part. */
export interface VikingBotAttachmentPart {
  /**
   * 片段类型，固定为 `attachment`。
   * Part type discriminator, always `attachment`.
   */
  type: 'attachment';
  /**
   * 文件名。
   * File name.
   */
  name: string;
  /**
   * 附件对应的资源 URI。
   * URI of the attachment resource.
   */
  uri?: string;
  /**
   * 附件预览地址。
   * Attachment preview URL.
   */
  previewUrl?: string;
  /**
   * MIME 类型。
   * MIME type.
   */
  mimeType?: string;
}

/** 图片片段。An image part. */
export interface VikingBotImagePart {
  /**
   * 片段类型，固定为 `image`。
   * Part type discriminator, always `image`.
   */
  type: 'image';
  /**
   * 图片地址，可以是 `viking://` URI、对象 URL 或宿主解析出的链接。
   * Image source: a `viking://` URI, an object URL or a host-resolved link.
   */
  src: string;
  /**
   * 图片说明，用作无障碍替代文本。
   * Image description, used as accessible alt text.
   */
  alt?: string;
  /**
   * 细节等级，沿用上游 `image_url.detail` 的语义。
   * Detail level, following the upstream `image_url.detail` semantics.
   */
  detail?: string;
}

/** 工具结果片段，与工具调用分开以保留事件到达顺序。A tool result part, kept apart from the call to preserve arrival order. */
export interface VikingBotToolResultPart {
  /**
   * 片段类型，固定为 `tool_result`。
   * Part type discriminator, always `tool_result`.
   */
  type: 'tool_result';
  /**
   * 对应的工具调用标识。
   * Id of the tool call this result belongs to.
   */
  id: string;
  /**
   * 工具名称。
   * Tool name.
   */
  name: string;
  /**
   * 工具输出文本。
   * Tool output text.
   */
  output: string;
  /**
   * 该结果是否为错误。
   * Whether the result is an error.
   */
  isError?: boolean;
}

/**
 * 消息中的一个内容片段，由宿主适配后传入。
 * One content part of a message, already adapted by the host.
 */
export type VikingBotMessagePart =
  /**
   * 纯文本。
   * Plain text.
   */
  | VikingBotTextPart
  /**
   * 推理过程。
   * Reasoning.
   */
  | VikingBotReasoningPart
  /**
   * 迭代标记。
   * Iteration marker.
   */
  | VikingBotIterationPart
  /**
   * 上下文引用。
   * Context reference.
   */
  | VikingBotContextPart
  /**
   * 工具调用。
   * Tool call.
   */
  | VikingBotToolPart
  /**
   * 工具结果。
   * Tool result.
   */
  | VikingBotToolResultPart
  /**
   * 图片。
   * Image.
   */
  | VikingBotImagePart
  /**
   * 附件。
   * Attachment.
   */
  | VikingBotAttachmentPart;

/** 一条消息。A single message. */
export interface VikingBotMessage {
  /**
   * 消息唯一标识。
   * Unique message key.
   */
  id: string;
  /**
   * 消息角色。
   * Message role.
   */
  role:
    /**
     * 用户消息。
     * A message from the user.
     */
    | 'user'
    /**
     * 助手消息。
     * A message from the assistant.
     */
    | 'assistant';
  /**
   * 消息的内容片段。
   * Content parts of the message.
   */
  parts: readonly VikingBotMessagePart[];
  /**
   * 把连续消息归入同一轮次；可选。
   * Groups consecutive messages into one turn; optional.
   */
  turnId?: string;
  /**
   * 消息创建时间。
   * Message creation time.
   */
  createdAt?: string;
  /**
   * 该助手消息是否仍在生成中。
   * Whether the assistant message is still being written.
   */
  streaming?: boolean;
}

/** 宿主组装好、等待发送的内容。A message composed by the host, waiting to be sent. */
export interface VikingBotDraft {
  /**
   * 文本内容。
   * Text content.
   */
  text: string;
  /**
   * 附带的附件。
   * Attached files.
   */
  attachments: readonly VikingBotAttachment[];
}

/** 一个附件。One attachment. */
export interface VikingBotAttachment {
  /**
   * 附件唯一标识。
   * Unique attachment key.
   */
  key: string;
  /**
   * 文件名。
   * File name.
   */
  name: string;
  /**
   * 本地对象 URL 或宿主解析出的预览地址。
   * Local object URL or a host-resolved preview link.
   */
  previewUrl?: string;
  /**
   * MIME 类型。
   * MIME type.
   */
  mimeType?: string;
  /**
   * 字节大小，未知时为 `null`。
   * Size in bytes, or `null` when unknown.
   */
  sizeBytes?: number | null;
  /**
   * 上传状态。
   * Upload status.
   */
  status?:
    /**
     * 等待上传。
     * Waiting to upload.
     */
    | 'pending'
    /**
     * 上传中。
     * Uploading.
     */
    | 'uploading'
    /**
     * 已就绪，可以发送。
     * Ready to be sent.
     */
    | 'ready'
    /**
     * 上传失败。
     * Upload failed.
     */
    | 'error';
  /**
   * 上传失败时的提示。
   * Message shown when the upload failed.
   */
  errorMessage?: string;
}

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface VikingBotLabels {
  /**
   * 输入框占位文本。
   * Input placeholder.
   */
  placeholder?: string;
  /**
   * 发送按钮文案。
   * Send button label.
   */
  send?: string;
  /**
   * 停止按钮文案。
   * Stop button label.
   */
  stop?: string;
  /**
   * 添加附件按钮文案。
   * Attach button label.
   */
  attach?: string;
  /**
   * 移除附件按钮文案。
   * Remove-attachment button label.
   */
  removeAttachment?: string;
  /**
   * 复制按钮文案。
   * Copy button label.
   */
  copy?: string;
  /**
   * 复制成功提示。
   * Message shown after a successful copy.
   */
  copied?: string;
  /**
   * 推理块标题。
   * Heading of the reasoning block.
   */
  reasoning?: string;
  /**
   * 生成中的提示。
   * Message shown while generating.
   */
  thinking?: string;
  /**
   * 工具调用标题。
   * Heading of a tool call.
   */
  tool?: string;
  /**
   * 工具待执行状态。
   * Text for a pending tool call.
   */
  toolPending?: string;
  /**
   * 工具执行中状态。
   * Text for a running tool call.
   */
  toolRunning?: string;
  /**
   * 工具执行成功状态。
   * Text for a completed tool call.
   */
  toolCompleted?: string;
  /**
   * 工具执行失败状态。
   * Text for a failed tool call.
   */
  toolError?: string;
  /**
   * 上下文引用标题。
   * Heading of a context reference.
   */
  context?: string;
  /**
   * 无消息时的提示。
   * Message shown when there are no messages.
   */
  empty?: string;
  /**
   * 新建会话按钮文案。
   * New-session button label.
   */
  newSession?: string;
  /**
   * 重试按钮文案。
   * Retry button label.
   */
  retry?: string;
  /**
   * 回到最新消息按钮文案。
   * Scroll-to-latest button label.
   */
  scrollToLatest?: string;
}

/**
 * 助手仍在生成时的增量片段。
 * Parts appended while the assistant is still streaming.
 */
export interface VikingBotStreamingState {
  /**
   * 已生成的片段。
   * Parts generated so far.
   */
  parts: readonly VikingBotMessagePart[];
  /**
   * 在 `messages` 中的哪个位置之后追加。
   * Index in `messages` the streaming parts are appended after.
   */
  insertIndex?: number;
  /**
   * 当前迭代轮次。
   * Current iteration number.
   */
  iteration: number;
}

/** 仅消息列表的公开接口。Public interface of the message list. */
export interface VikingBotMessageListProps {
  /**
   * 附加到根元素的类名。
   * Class name applied to the root element.
   */
  className?: string;
  /**
   * 当前会话的消息。
   * Messages in the current session.
   */
  messages: readonly VikingBotMessage[];
  /**
   * 助手仍在生成时追加的片段。
   * Parts appended while the assistant is still streaming.
   */
  streaming?: VikingBotStreamingState;
  /**
   * 渲染在最后一条消息之后，例如记忆提交提示条。
   * Rendered after the last message, for example a memory-commit banner.
   */
  footer?: ReactNode;
  /**
   * 身份作用域变化时强制重新挂载。
   * Forces a remount when the identity scope changes.
   */
  listKey?: string;
  /**
   * 长历史是否使用虚拟列表。
   * Uses a virtualised list for long histories.
   */
  virtualized?: boolean;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: VikingBotLabels;
  /**
   * 打开消息中引用的资源。
   * Opens a resource referenced by a message.
   */
  onOpenUri?: (uri: string) => void;
  /**
   * 复制由宿主执行，列表只上报意图。
   * The host performs the copy; the list only reports the intent.
   */
  onCopy?: (text: string) => void;
  /**
   * 重试一条消息。
   * Retries a message.
   */
  onRetry?: (messageId: string) => void;
  /**
   * 助手消息上的宿主自有动作，例如提交记忆。
   * Host-owned action on an assistant message, for example commit memory.
   */
  onMessageAction?: (messageId: string) => void;
}

/** 输入区的命令式入口。Imperative handle of the composer. */
export interface VikingBotComposerRef {
  /**
   * 替换输入文本，例如快捷操作预填指令。
   * Replaces the input text, for example when a quick action prefills it.
   */
  setText: (text: string) => void;
  /**
   * 添加在输入区外拖入的文件。
   * Adds files dropped outside the input area.
   */
  addFiles: (files: readonly File[]) => void;
  /**
   * 是否已达到附件数量上限。
   * Whether the attachment limit has been reached.
   */
  isAttachmentLimitReached: () => boolean;
  /**
   * 清空输入框与待发送附件。
   * Clears the input and pending attachments.
   */
  clear: () => void;
  /**
   * 聚焦输入框。
   * Focuses the input.
   */
  focus: () => void;
}

/** 仅输入区的公开接口。Public interface of the composer. */
export interface VikingBotComposerProps {
  /**
   * 附加到根元素的类名。
   * Class name applied to the root element.
   */
  className?: string;
  /**
   * 受控的草稿文本。
   * Controlled draft text.
   */
  value: string;
  /**
   * 宿主已选中的附件。
   * Attachments already selected by the host.
   */
  attachments?: readonly VikingBotAttachment[];
  /**
   * 是否禁用发送；原因与提示由宿主决定。
   * Disables sending; the host owns the reason and the message.
   */
  disabled?: boolean;
  /**
   * 禁用时展示的说明。
   * Reason shown while the composer is disabled.
   */
  disabledReason?: string;
  /**
   * 是否正在回复；回复中停止动作替代发送。
   * Whether a reply is streaming; the stop action replaces send.
   */
  isSending?: boolean;
  /**
   * 输入区布局形态。
   * Layout of the input area.
   */
  variant?:
    /**
     * 默认布局。
     * Default layout.
     */
    | 'default'
    /**
     * 紧凑布局。
     * Compact layout.
     */
    | 'compact';
  /**
   * 宿主接受的 MIME 类型；缺省为常见附件集合。
   * MIME types the host accepts; defaults to a broad attachment set.
   */
  accept?: string;
  /**
   * 宿主允许的最大附件数。
   * Maximum number of attachments the host allows.
   */
  maxAttachments?: number;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: VikingBotLabels;
  /**
   * 发送按钮旁的额外控件。
   * Extra controls rendered next to the send button.
   */
  actions?: ReactNode;
  /**
   * 输入值变化时触发。
   * Invoked when the input value changes.
   */
  onValueChange: (value: string) => void;
  /**
   * 上报用户选择或拖入的文件；由宿主上传。
   * Reports files the user picked or dropped; the host uploads them.
   */
  onFilesAdded?: (files: readonly File[]) => void;
  /**
   * 移除一个附件。
   * Removes an attachment.
   */
  onAttachmentRemove?: (attachment: VikingBotAttachment) => void;
  /**
   * 上报发送意图；由宿主发送并清空草稿。
   * Reports the send intent; the host sends and clears the draft.
   */
  onSend: (draft: VikingBotDraft) => void;
  /**
   * 停止正在进行的回复。
   * Stops the streaming reply.
   */
  onCancel: () => void;
}

/** 完整对话区的公开接口。Public interface of the whole conversation view. */
export interface VikingBotProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onCopy' | 'onSubmit'
> {
  /**
   * 当前会话的消息。
   * Messages in the current session.
   */
  messages: readonly VikingBotMessage[];
  /**
   * 助手仍在生成时追加的片段。
   * Parts appended while the assistant is still streaming.
   */
  streaming?: VikingBotStreamingState;
  /**
   * 受控的草稿文本；不传则不展示输入区。
   * Controlled draft text; omit when the composer is not shown.
   */
  draft?: string;
  /**
   * 宿主已选中的附件。
   * Attachments already selected by the host.
   */
  attachments?: readonly VikingBotAttachment[];
  /**
   * 是否正在回复。
   * Whether a reply is streaming.
   */
  isSending?: boolean;
  /**
   * 是否可接受输入；不可用时由宿主给出原因。
   * Whether the bot can accept input; the host supplies the reason when not.
   */
  isAvailable?: boolean;
  /**
   * 不可用时的说明。
   * Message shown while the bot is unavailable.
   */
  unavailableMessage?: string;
  /**
   * 首条消息之前替代消息列表显示的内容。
   * Shown instead of the message list before the first message.
   */
  emptyState?: ReactNode;
  /**
   * 头部插槽，例如会话标题或历史按钮。
   * Header slot, for example a session title or history button.
   */
  header?: ReactNode;
  /**
   * 渲染在输入区上方，例如记忆提交提示条。
   * Rendered above the composer, for example a memory-commit banner.
   */
  footer?: ReactNode;
  /**
   * 替换整个主体，用于宿主自定义组合。
   * Replaces the whole body for host-specific composition.
   */
  children?: ReactNode;
  /**
   * 身份作用域变化时强制重新挂载消息列表。
   * Forces a remount of the message list when the identity scope changes.
   */
  listKey?: string;
  /**
   * 长历史是否使用虚拟列表。
   * Uses a virtualised list for long histories.
   */
  virtualized?: boolean;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: VikingBotLabels;
  /**
   * 输入区的命令式入口。
   * Imperative handle of the composer.
   */
  composerRef?: RefObject<VikingBotComposerRef | null>;
  /**
   * 草稿变化时触发。
   * Invoked when the draft changes.
   */
  onDraftChange?: (value: string) => void;
  /**
   * 上报用户选择或拖入的文件；由宿主上传。
   * Reports files the user picked or dropped; the host uploads them.
   */
  onFilesAdded?: (files: readonly File[]) => void;
  /**
   * 移除一个附件。
   * Removes an attachment.
   */
  onAttachmentRemove?: (attachment: VikingBotAttachment) => void;
  /**
   * 上报发送意图。
   * Reports the send intent.
   */
  onSend?: (draft: VikingBotDraft) => void;
  /**
   * 停止正在进行的回复。
   * Stops the streaming reply.
   */
  onCancel?: () => void;
  /**
   * 打开消息中引用的资源。
   * Opens a resource referenced by a message.
   */
  onOpenUri?: (uri: string) => void;
  /**
   * 复制由宿主执行，组件只上报意图。
   * The host performs the copy; the component only reports the intent.
   */
  onCopy?: (text: string) => void;
  /**
   * 重试一条消息。
   * Retries a message.
   */
  onRetry?: (messageId: string) => void;
}
