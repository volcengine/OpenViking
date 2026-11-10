import type { Locale } from '../../../shared/locale.js';
import type { ReactNode } from 'react';

/** 可选项。An option offered by a question. */
export interface VikingBotInteractionOption {
  /** 选项值。Option value. */
  value: string;
  /** 选项主文案。Primary label. */
  label: ReactNode;
  /** 选项下方的说明。Description shown under the label. */
  description?: ReactNode;
  /** 选项后附加的等宽片段，例如命令。Monospace snippet after the label. */
  code?: string;
}

/** 定时值。A schedule value. */
export type VikingBotScheduleValue =
  | {
      /** 不启用。Disabled. */
      mode: 'none';
    }
  | {
      /** 每天。Daily. */
      mode: 'daily';
      /** 时间，`HH:mm`。Time as `HH:mm`. */
      time: string;
    }
  | {
      /** 每周。Weekly. */
      mode: 'weekly';
      /** 时间，`HH:mm`。Time as `HH:mm`. */
      time: string;
      /** 星期，1 为周一。Weekday, 1 is Monday. */
      weekday: 1 | 2 | 3 | 4 | 5 | 6 | 7;
    };

/** 修复候选。A repair candidate. */
export interface VikingBotRepairOption {
  /** 候选标识。Candidate key. */
  id: string;
  /** 候选文案。Candidate label. */
  label: string;
  /** 来源说明。Source description. */
  description?: string;
}

/** 自定义问题的渲染上下文。Render context for a custom question. */
export interface VikingBotInteractionRenderContext {
  /** 是否禁用提交。Whether submission is disabled. */
  disabled?: boolean;
  /** 是否正在提交。Whether a submission is in flight. */
  submitting?: boolean;
  /** 上报答案；由宿主转成它自己的协议。Reports an answer; the host adapts it. */
  submit: (value: unknown) => void | Promise<void>;
  /** 放弃本次交互。Abandons this interaction. */
  cancel: () => void;
}

/**
 * 一次待确认的交互。组件内置 `choice`、`text`、`schedule`、`repair` 四种表单；
 * 需要树选择器、上传等宿主能力时用 `custom` 自行渲染。
 * One pending interaction. The component ships `choice`, `text`, `schedule` and
 * `repair`; use `custom` when the form needs host capabilities such as a tree
 * picker or an upload.
 */
export type VikingBotInteractionRequest =
  | {
      /** 单选。Single choice. */
      kind: 'choice';
      /** 问题文案。Question copy. */
      question?: ReactNode;
      /** 选项。Options. */
      options: readonly VikingBotInteractionOption[];
      /** 是否提供「其他」输入项。Whether an inline "other" input is offered. */
      allowCustomInput?: boolean | { placeholder?: string };
    }
  | {
      /** 文本输入。Free text. */
      kind: 'text';
      /** 问题文案。Question copy. */
      question?: ReactNode;
      /** 初始值。Initial value. */
      initialValue?: string;
      /** 补充说明。Extra description. */
      description?: ReactNode;
      /** 是否多行。Whether the input is multiline. */
      multiline?: boolean;
    }
  | {
      /** 定时。Schedule. */
      kind: 'schedule';
      /** 问题文案。Question copy. */
      question?: ReactNode;
      /** 时区。Time zone. */
      timezone?: string;
      /** 初始值。Initial value. */
      initialValue: VikingBotScheduleValue;
    }
  | {
      /** 从候选修复项中选择。Pick one repair candidate. */
      kind: 'repair';
      /** 问题文案。Question copy. */
      question?: ReactNode;
      /** 候选。Candidates. */
      options: readonly VikingBotRepairOption[];
      /** 初始选中的候选标识。Initially selected candidate key. */
      initialValue?: string;
    }
  | {
      /** 由宿主渲染。Rendered by the host. */
      kind: 'custom';
      /** 问题文案。Question copy. */
      question?: ReactNode;
      /** 渲染函数。Renderer. */
      render: (context: VikingBotInteractionRenderContext) => ReactNode;
    };

/** 交互答案。An interaction answer. */
export type VikingBotInteractionAnswer =
  | { kind: 'choice'; value: string }
  | { kind: 'text'; value: string }
  | { kind: 'schedule'; value: VikingBotScheduleValue }
  | { kind: 'repair'; value: string }
  | { kind: 'custom'; value: unknown };

/**
 * 交互区文案。组件内置中英文文案，宿主可按需覆盖。
 * Copy of the interaction area. The component ships Chinese and English defaults; a host
 * overrides any field it needs.
 */
export interface VikingBotInteractionLabels {
  /** 文本答案的无障碍名称。Accessible name of a text answer. */
  answer?: string;
  /** 区域标题，缺省为「待确认」。Area title, "Needs your input" by default. */
  title?: string;
  /** 提交按钮。Submit button. */
  submit?: string;
  /** 取消按钮。Cancel button. */
  cancel?: string;
  /** 「其他」输入项文案。Label of the "other" input. */
  other?: string;
  /** 提交成功提示。Shown after a successful submission. */
  submitted?: string;
  /** 取消提示。Shown after cancelling. */
  cancelled?: string;
  /** 步骤进度，接收当前序号与总数。Step progress, given index and total. */
  progress?: (index: number, total: number) => string;
  /** 不启用定时的选项文案。Label of the "no schedule" option. */
  scheduleNone?: string;
  /** 每天的选项文案。Label of the daily option. */
  scheduleDaily?: string;
  /** 每周的选项文案。Label of the weekly option. */
  scheduleWeekly?: string;
  /** 时间输入的无障碍名称。Accessible name of the time input. */
  scheduleTime?: string;
  /** 星期选择的无障碍名称。Accessible name of the weekday select. */
  scheduleWeekday?: string;
  /** 星期名称，按周一至周日排列。Weekday names, Monday first. */
  weekdays?: readonly string[];
}

/**
 * 待确认交互区。宿主从自己的流里算出问题，这里只负责表单与状态；
 * 答案通过 `onSubmit` 上报，宿主决定怎么发回去。
 * The pending-interaction area. The host derives the question from its own
 * stream; this renders the form and its state, and reports the answer.
 */
export interface VikingBotInteractionProps {
  /** 当前问题。The current question. */
  request: VikingBotInteractionRequest;
  /** 内置中英文文案的语言。Language of the built-in interface copy. */
  locale: Locale;
  /**
   * 问题的稳定身份；变化时重置表单状态。省略时按 `request.kind` 与序号判定。
   * Stable identity of the question; changes reset the form state. Defaults to
   * the request kind plus the step index.
   */
  requestKey?: string;
  /** 附加到根元素的类名。Class name applied to the root element. */
  className?: string;
  /** 当前步骤序号，从 0 开始。Current step index, starting at 0. */
  index?: number;
  /** 步骤总数。Total number of steps. */
  total?: number;
  /** 是否禁用。Whether the form is disabled. */
  disabled?: boolean;
  /** 是否正在提交。Whether a submission is in flight. */
  submitting?: boolean;
  /** 提交失败的原因。Why the last submission failed. */
  error?: ReactNode;
  /** 上报答案。Reports an answer. */
  onSubmit?: (answer: VikingBotInteractionAnswer) => void | Promise<void>;
  /** 放弃本次交互。Abandons this interaction. */
  onCancel?: () => void;
  /** 可替换文案，未提供的项使用当前 locale 的内置文案。Replaceable copy; omitted fields follow locale. */
  labels?: VikingBotInteractionLabels;
}
