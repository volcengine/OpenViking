import type { FormHTMLAttributes, ReactNode } from 'react';

/**
 * 一个导入来源。组件不内置来源列表，由宿主声明提供哪些、叫什么、带什么字段。
 *
 * One import source. The component ships no source list; the host declares
 * which sources exist, what they are called and which fields they carry.
 */
export interface ResourceImportSource {
  /**
   * 来源唯一标识，由宿主定义并在 `onSourceChange` 中回传。
   * Unique source key defined by the host and reported back through
   * `onSourceChange`.
   */
  id: string;
  /**
   * 来源显示文本。
   * Source label.
   */
  label: string;
  /**
   * 来源说明。
   * Source description.
   */
  description?: string;
  /**
   * 来源图标。
   * Source icon.
   */
  icon?: ReactNode;
  /**
   * 是否禁用该来源。
   * Whether the source is disabled.
   */
  disabled?: boolean;
  /**
   * 该来源专属的字段，按顺序渲染在通用字段之后。
   * Fields specific to this source, rendered after the common ones.
   */
  fields?: readonly ResourceImportField[];
  /**
   * 来源专属的校验失败提示。宿主校验，组件只负责展示。
   * Validation message for this source. The host validates; the component only
   * displays the result.
   */
  errorMessage?: string;
}

/**
 * 一个表单字段。组件提供常见控件，其余由宿主渲染任意内容。
 *
 * A form field. The component offers common controls; anything else is
 * rendered by the host.
 */
export interface ResourceImportField {
  /**
   * 字段唯一标识，也是取值时的键。
   * Unique field key, also the key in the value map.
   */
  key: string;
  /**
   * 字段标签。
   * Field label.
   */
  label: string;
  /**
   * 字段类型，决定使用哪种控件。
   * Field type, which selects the control.
   */
  type:
    /**
     * 单行文本。
     * Single-line text.
     */
    | 'text'
    /**
     * 多行文本。
     * Multi-line text.
     */
    | 'textarea'
    /**
     * 开关。
     * Toggle.
     */
    | 'switch'
    /**
     * 下拉选择。
     * Select.
     */
    | 'select'
    /**
     * 数字输入。
     * Number input.
     */
    | 'number'
    /**
     * 目录选择。
     * Directory picker.
     */
    | 'directory'
    /**
     * 宿主自定义渲染。
     * Host-defined rendering.
     */
    | 'custom';
  /**
   * 当前值。文本为字符串，开关为布尔，数字为 number。
   * Current value: a string for text, a boolean for switches, a number for
   * numeric inputs.
   */
  value?: string | number | boolean | null;
  /**
   * 占位文本。
   * Placeholder text.
   */
  placeholder?: string;
  /**
   * 字段说明，显示在控件下方。
   * Field description, shown under the control.
   */
  description?: string;
  /**
   * 校验失败提示。
   * Validation message.
   */
  errorMessage?: string;
  /**
   * 是否禁用该字段。
   * Whether the field is disabled.
   */
  disabled?: boolean;
  /**
   * 是否必填。
   * Whether the field is required.
   */
  required?: boolean;
  /**
   * `select` 的可选项。
   * Options for `select`.
   */
  options?: readonly { value: string; label: string }[];
  /**
   * `custom` 类型的内容，由宿主渲染。
   * Content for `custom`, rendered by the host.
   */
  content?: ReactNode;
  /**
   * 值变化时触发。目录字段通过它回传选中的 URI。
   * Invoked when the value changes. Directory fields report the picked URI
   * through it.
   */
  onChange?: (value: string | number | boolean | null) => void;
  /**
   * `directory` 类型的选取入口，由宿主打开目录选择器。
   * Pick handler for `directory`, where the host opens its directory picker.
   */
  onPick?: () => void;
}

/** 宿主已加入导入队列的文件。A file the host has accepted into the import queue. */
export interface ResourceImportFile {
  /**
   * 文件唯一标识。
   * Unique file key.
   */
  id: string;
  /**
   * 文件名。
   * File name.
   */
  name: string;
  /**
   * 字节大小，未知时为 `null`。
   * Size in bytes, or `null` when unknown.
   */
  sizeBytes?: number | null;
  /**
   * MIME 类型，未知时为 `null`。
   * MIME type, or `null` when unknown.
   */
  mimeType?: string | null;
  /**
   * 上传状态。
   * Upload status.
   */
  status:
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
     * 上传成功。
     * Uploaded successfully.
     */
    | 'success'
    /**
     * 上传失败。
     * Upload failed.
     */
    | 'error';
  /**
   * 上传进度，无法上报时为 `null`。
   * Upload progress, or `null` when the host cannot report it.
   */
  progress?: number | null;
  /**
   * 上传失败时的提示。
   * Message shown when the upload failed.
   */
  errorMessage?: string;
}

/**
 * 一次导入任务。任务标识由宿主提供，组件只负责按状态渲染。
 *
 * An import task. The host supplies the task id; the component only renders by
 * status.
 */
export interface ResourceImportTask {
  /**
   * 任务唯一标识。
   * Unique task key.
   */
  id: string;
  /**
   * 任务名称，通常是文件名或来源地址。
   * Task name, usually a file name or a source URL.
   */
  name: string;
  /**
   * 任务状态。
   * Task status.
   */
  status:
    /**
     * 等待开始。
     * Waiting to start.
     */
    | 'pending'
    /**
     * 上传中。
     * Uploading.
     */
    | 'uploading'
    /**
     * 服务端处理中。
     * Processing on the server.
     */
    | 'processing'
    /**
     * 已完成。
     * Finished successfully.
     */
    | 'success'
    /**
     * 失败。
     * Finished with an error.
     */
    | 'error'
    /**
     * 已取消。
     * Cancelled by the user.
     */
    | 'cancelled';
  /**
   * 进度，0–1；宿主无法上报时为 `null`。
   * Progress from 0 to 1, or `null` when the host cannot report it.
   */
  progress?: number | null;
  /**
   * 失败原因。
   * Failure reason.
   */
  errorMessage?: string;
  /**
   * 任务完成后的目标 URI。
   * Destination URI once the task finishes.
   */
  resultUri?: string;
  /**
   * 任务创建时间。
   * Task creation time.
   */
  createdAt?: string;
}

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface ResourceImportLabels {
  /**
   * 表单标题。
   * Form title.
   */
  title?: string;
  /**
   * 表单副标题。
   * Form subtitle.
   */
  subtitle?: string;
  /**
   * 来源选择区域的标题。
   * Heading of the source picker.
   */
  sourceLabel?: string;
  /**
   * 文件选择区域的标题。
   * Heading of the file picker.
   */
  filesLabel?: string;
  /**
   * 拖拽区提示。
   * Drop zone hint.
   */
  dropHint?: string;
  /**
   * 浏览文件按钮文案。
   * Browse-files button label.
   */
  browse?: string;
  /**
   * 移除文件按钮文案。
   * Remove-file button label.
   */
  removeFile?: string;
  /**
   * 目标目录字段的标签。
   * Label of the destination field.
   */
  destinationLabel?: string;
  /**
   * 更换目标目录的按钮文案。
   * Change-destination button label.
   */
  changeDestination?: string;
  /**
   * 提交按钮文案。
   * Submit button label.
   */
  submit?: string;
  /**
   * 取消按钮文案。
   * Cancel button label.
   */
  cancel?: string;
  /**
   * 重试按钮文案。
   * Retry button label.
   */
  retry?: string;
  /**
   * 清空按钮文案。
   * Clear button label.
   */
  clear?: string;
  /**
   * 上传中状态文本。
   * Text for the uploading status.
   */
  uploading?: string;
  /**
   * 处理中状态文本。
   * Text for the processing status.
   */
  processing?: string;
  /**
   * 成功状态文本。
   * Text for the success status.
   */
  success?: string;
  /**
   * 失败状态文本。
   * Text for the error status.
   */
  error?: string;
  /**
   * 未选择文件时的提示。
   * Message shown when no file is selected.
   */
  noFiles?: string;
  /**
   * 任务列表标题。
   * Task list title.
   */
  taskListTitle?: string;
  /**
   * 任务列表为空时的提示。
   * Message shown when the task list is empty.
   */
  taskListEmpty?: string;
}

/** 来源选择与表单字段，不含文件与任务状态。Source picker and form fields, without any file or task state. */
export interface ResourceImportFormProps extends Omit<
  FormHTMLAttributes<HTMLFormElement>,
  'onSubmit'
> {
  /**
   * 提供的来源，按渲染顺序排列。
   * Sources to offer, in render order.
   */
  sources: readonly ResourceImportSource[];
  /**
   * 受控的当前来源 id。
   * Controlled active source id.
   */
  activeSourceId: string;
  /**
   * 通用字段，渲染在所有来源字段之前。
   * Common fields, rendered before the source-specific ones.
   */
  fields?: readonly ResourceImportField[];
  /**
   * 是否正在提交；提交中禁用操作按钮。
   * Whether the form is submitting; disables the actions.
   */
  submitting?: boolean;
  /**
   * 提交失败时展示在操作区上方的提示。
   * Shown above the actions when the submit failed.
   */
  errorMessage?: string;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: ResourceImportLabels;
  /**
   * 提交按钮旁的额外动作，例如监听开关。
   * Extra actions next to submit, for example a watch toggle.
   */
  actions?: ReactNode;
  /**
   * 切换来源时触发。
   * Invoked when the active source changes.
   */
  onSourceChange: (sourceId: string) => void;
  /**
   * 提交导入。
   * Submits the import.
   */
  onSubmit: () => void;
  /**
   * 取消导入。
   * Cancels the import.
   */
  onCancel?: () => void;
}

/** 拖拽区与已选文件列表，不含来源专属字段。Drop zone and selected-file list, without any source-specific fields. */
export interface ResourceImportFilePickerProps {
  /**
   * 附加到根元素的类名。
   * Class name applied to the root element.
   */
  className?: string;
  /**
   * 已选中的文件。
   * Selected files.
   */
  files: readonly ResourceImportFile[];
  /**
   * 宿主接受的 MIME 类型。
   * MIME types the host accepts.
   */
  accept?: string;
  /**
   * 宿主允许的最大文件数。
   * Maximum number of files the host allows.
   */
  maxFiles?: number;
  /**
   * 单文件最大字节数。
   * Maximum size per file in bytes.
   */
  maxFileSizeBytes?: number;
  /**
   * 是否禁用选择。
   * Whether picking is disabled.
   */
  disabled?: boolean;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: ResourceImportLabels;
  /**
   * 上报用户选择或拖入的文件。
   * Reports files the user picked or dropped.
   */
  onFilesAdded: (files: readonly File[]) => void;
  /**
   * 移除一个已选文件。
   * Removes a selected file.
   */
  onFileRemove?: (file: ResourceImportFile) => void;
}

/** 进行中与已完成导入任务的进度列表。Progress list for running and finished import tasks. */
export interface ResourceImportTaskListProps {
  /**
   * 附加到根元素的类名。
   * Class name applied to the root element.
   */
  className?: string;
  /**
   * 任务列表。
   * Task list.
   */
  tasks: readonly ResourceImportTask[];
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: ResourceImportLabels;
  /**
   * 是否以弹窗形态展示。
   * Renders the list in a dialog shell.
   */
  open?: boolean;
  /**
   * 弹窗开关变化时触发。
   * Invoked when the dialog opens or closes.
   */
  onOpenChange?: (open: boolean) => void;
  /**
   * 重试一个失败任务。
   * Retries a failed task.
   */
  onRetry?: (task: ResourceImportTask) => void;
  /**
   * 取消一个进行中的任务。
   * Cancels a running task.
   */
  onCancel?: (task: ResourceImportTask) => void;
  /**
   * 清除已完成的任务。
   * Clears finished tasks.
   */
  onClearFinished?: () => void;
}

/**
 * 组合形态：来源、文件、目标目录与操作按钮。
 * Composed import surface: sources, files, destination and actions.
 */
export interface ResourceImportProps extends ResourceImportFormProps {
  /**
   * 已选中的文件。
   * Selected files.
   */
  files?: readonly ResourceImportFile[];
  /**
   * 宿主接受的 MIME 类型。
   * MIME types the host accepts.
   */
  accept?: string;
  /**
   * 宿主允许的最大文件数。
   * Maximum number of files the host allows.
   */
  maxFiles?: number;
  /**
   * 单文件最大字节数。
   * Maximum size per file in bytes.
   */
  maxFileSizeBytes?: number;
  /**
   * 上报用户选择或拖入的文件。
   * Reports files the user picked or dropped.
   */
  onFilesAdded?: (files: readonly File[]) => void;
  /**
   * 移除一个已选文件。
   * Removes a selected file.
   */
  onFileRemove?: (file: ResourceImportFile) => void;
}
