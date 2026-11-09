import type { ReactNode } from 'react';

/**
 * 片段类型判别值。两种宿主风格共用这一层：宿主在接入层把各自的流事件
 * 归一化成这些片段，组件不依赖任一方协议。
 * Part discriminators shared by both host styles. Each host normalizes its own
 * stream events into these parts; the component depends on neither protocol.
 */

/** 纯文本片段。A plain text part. */
export interface VikingBotTextPart {
  /** 片段类型。Part type. */
  type: 'text';
  /** 文本内容。Text content. */
  text: string;
  /**
   * 文本格式，缺省为 `plain`。开源版的回答是 Markdown，火山版的工具输出是纯文本。
   * Text format, `plain` by default. Open-source answers are Markdown while
   * managed-agent tool output is plain text.
   */
  format?: 'plain' | 'markdown';
}

/** 推理过程片段。A reasoning part. */
export interface VikingBotReasoningPart {
  /** 片段类型。Part type. */
  type: 'reasoning';
  /** 推理文本。Reasoning text. */
  text: string;
  /** 是否仍在生成。Whether the reasoning is still being generated. */
  running?: boolean;
  /** 所属执行块标识，同组片段合并展示。Owning activity block; same group collapses together. */
  groupId?: string;
  /** 开始时间，毫秒或 ISO 字符串。Start time in milliseconds or ISO text. */
  startedAt?: number | string;
  /** 结束时间。End time. */
  completedAt?: number | string;
  /** 耗时，单位毫秒。Duration in milliseconds. */
  durationMs?: number;
}

/** Agent 循环的迭代标记。An agent-loop iteration marker. */
export interface VikingBotIterationPart {
  /** 片段类型。Part type. */
  type: 'iteration';
  /** 迭代轮次。Iteration number. */
  iteration: number;
}

/** 上下文引用片段。A context reference part. */
export interface VikingBotContextPart {
  /** 片段类型。Part type. */
  type: 'context';
  /** 被引用的资源 URI。URI of the referenced resource. */
  uri: string;
  /** 引用类别。Reference category. */
  contextType: 'memory' | 'resource' | 'skill';
  /** 引用显示名称，缺省时由 URI 推导。Display label, derived from the URI when omitted. */
  label?: string;
  /** 引用摘要。Reference abstract. */
  abstract?: string;
}

/** 工具调用片段。A tool call part. */
export interface VikingBotToolPart {
  /** 片段类型。Part type. */
  type: 'tool';
  /** 工具调用唯一标识。Unique tool call key. */
  id: string;
  /** 工具名称。Tool name. */
  name: string;
  /** 调用状态。Call status. */
  status: 'pending' | 'running' | 'completed' | 'error';
  /** 调用入参。Call input. */
  input?: Record<string, unknown>;
  /** 调用输出文本。Call output text. */
  output?: string;
  /** 调用耗时，单位毫秒。Call duration in milliseconds. */
  durationMs?: number;
  /** 所属执行块标识。Owning activity block. */
  groupId?: string;
  /** MCP 服务名，火山版按服务分组展示。MCP server name; the managed agent groups by it. */
  server?: string;
  /** 该调用对应的后端接口，用于展示可点击的接口标签。Backend endpoint, shown as a tag. */
  endpoint?: { method: string; path: string };
}

/** 工具结果片段，与调用分开以保留到达顺序。A tool result part, kept apart to preserve arrival order. */
export interface VikingBotToolResultPart {
  /** 片段类型。Part type. */
  type: 'tool_result';
  /** 对应的工具调用标识。Id of the tool call this result belongs to. */
  id: string;
  /** 工具名称。Tool name. */
  name: string;
  /** 工具输出文本。Tool output text. */
  output: string;
  /** 该结果是否为错误。Whether the result is an error. */
  isError?: boolean;
  /** 所属执行块标识。Owning activity block. */
  groupId?: string;
}

/** 图片片段。An image part. */
export interface VikingBotImagePart {
  /** 片段类型。Part type. */
  type: 'image';
  /** 图片地址。Image source. */
  src: string;
  /** 图片说明，用作无障碍替代文本。Accessible alt text. */
  alt?: string;
  /** 图片细节等级。Image detail level. */
  detail?: string;
}

/** 附件片段。An attachment part. */
export interface VikingBotAttachmentPart {
  /** 片段类型。Part type. */
  type: 'attachment';
  /** 文件名。File name. */
  name: string;
  /** 附件对应的资源 URI。URI of the attachment resource. */
  uri?: string;
  /** 附件预览地址。Attachment preview URL. */
  previewUrl?: string;
  /** MIME 类型。MIME type. */
  mimeType?: string;
}

/** 消息中的一个内容片段。One content part of a message. */
export type VikingBotMessagePart =
  | VikingBotTextPart
  | VikingBotReasoningPart
  | VikingBotIterationPart
  | VikingBotContextPart
  | VikingBotToolPart
  | VikingBotToolResultPart
  | VikingBotImagePart
  | VikingBotAttachmentPart;

/** 一条消息。A single message. */
export interface VikingBotMessage {
  /** 消息唯一标识。Unique message key. */
  id: string;
  /**
   * 消息角色。开源版只有 user 和 assistant；火山版额外有 system 提示与
   * tool 独立块，因此这里保留四类。
   * Message role. The open-source host has user and assistant only; the managed
   * agent also emits system notices and standalone tool blocks.
   */
  role: 'user' | 'assistant' | 'system' | 'tool';
  /** 消息的内容片段。Content parts of the message. */
  parts: readonly VikingBotMessagePart[];
  /** 把连续消息归入同一轮次。Groups consecutive messages into one turn. */
  turnId?: string;
  /** 消息创建时间。Message creation time. */
  createdAt?: string;
  /** 该助手消息是否仍在生成中。Whether the assistant message is still being written. */
  streaming?: boolean;
  /**
   * system 消息的语气，缺省为 `info`；`error` 用于连接中断等提示。
   * Tone of a system message, `info` by default; `error` for failures.
   */
  tone?: 'info' | 'error';
  /**
   * 合并后的消息携带的原始动作清单；合并时保留每条消息自己的 messageId，
   * 渲染时按原始消息回传，避免把动作挂到错误的消息上。
   * Actions carried by the messages this one merged, each keeping its original
   * message id so the host receives the right one.
   */
  actions?: readonly {
    /** 原始消息标识。Original message key. */
    messageId: string;
    /** 该消息声明的动作。Action declared by that message. */
    action: NonNullable<VikingBotMessage['action']>;
  }[];
  /**
   * 消息级动作，由宿主声明。火山版用它在助手消息下挂「提交会话」。
   * Message-level action declared by the host. The managed agent uses it for
   * the memory-commit control under an assistant message.
   */
  action?: {
    /** 动作标识。Action key. */
    key: string;
    /** 动作文案。Action label. */
    label?: ReactNode;
    /** 动作状态。Action status. */
    status?: 'idle' | 'pending' | 'submitted' | 'success' | 'error';
    /** 不可用时的说明。Reason shown when unavailable. */
    disabledReason?: string;
  };
}

/** 助手仍在生成时的增量片段。Parts appended while the assistant is still streaming. */
export interface VikingBotStreamingState {
  /** 已生成的片段。Parts generated so far. */
  parts: readonly VikingBotMessagePart[];
  /** 在 `messages` 中的哪个位置之后追加。Index the parts are appended after. */
  insertIndex?: number;
  /** 当前迭代轮次。Current iteration number. */
  iteration: number;
}

/** 一个附件。One attachment. */
export interface VikingBotAttachment {
  /** 附件唯一标识。Unique attachment key. */
  key: string;
  /** 文件名。File name. */
  name: string;
  /** 本地对象 URL 或宿主解析出的预览地址。Local object URL or a host-resolved preview link. */
  previewUrl?: string;
  /** MIME 类型。MIME type. */
  mimeType?: string;
  /** 字节大小，未知时为 `null`。Size in bytes, or `null` when unknown. */
  sizeBytes?: number | null;
  /** 上传状态。Upload status. */
  status?: 'pending' | 'uploading' | 'ready' | 'error';
  /** 上传失败时的提示。Message shown when the upload failed. */
  errorMessage?: string;
}

/** 宿主组装好、等待发送的内容。A message composed by the host, waiting to be sent. */
export interface VikingBotDraft {
  /** 文本内容。Text content. */
  text: string;
  /** 附带的附件。Attached files. */
  attachments: readonly VikingBotAttachment[];
}

/** 可替换文案，组件内置英文默认值。Replaceable copy; the component ships English defaults. */
export interface VikingBotLabels {
  /** 输入框占位文本。Input placeholder. */
  placeholder?: string;
  /** 发送按钮文案。Send button label. */
  send?: string;
  /** 停止按钮文案。Stop button label. */
  stop?: string;
  /** 添加附件按钮文案。Attach button label. */
  attach?: string;
  /** 移除附件按钮文案。Remove-attachment button label. */
  removeAttachment?: string;
  /** 复制按钮文案。Copy button label. */
  copy?: string;
  /** 复制成功提示。Message shown after a successful copy. */
  copied?: string;
  /** 推理块标题。Heading of the reasoning block. */
  reasoning?: string;
  /** 生成中的提示。Message shown while generating. */
  thinking?: string;
  /** 工具调用标题。Heading of a tool call. */
  tool?: string;
  /** 工具待执行状态。Text for a pending tool call. */
  toolPending?: string;
  /** 工具执行中状态。Text for a running tool call. */
  toolRunning?: string;
  /** 工具执行成功状态。Text for a completed tool call. */
  toolCompleted?: string;
  /** 工具执行失败状态。Text for a failed tool call. */
  toolError?: string;
  /** 上下文引用标题。Heading of a context reference. */
  context?: string;
  /** 无消息时的提示。Message shown when there are no messages. */
  empty?: string;
  /** 新建会话按钮文案。New-session button label. */
  newSession?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
  /** 回到最新消息按钮文案。Scroll-to-latest button label. */
  scrollToLatest?: string;
  /** 不可用时的提示。Message shown while the bot is unavailable. */
  unavailable?: string;
}
