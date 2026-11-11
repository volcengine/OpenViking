import type { FilePreviewLabels } from '../types.js';

const messages = {
  showHiddenPath: '显示隐藏的路径层级',
  copyPath: '复制路径',
  copyContent: '复制正文',
  copied: '已复制',
  copyFailed: '复制失败',
  previewMode: '预览',
  sourceMode: '源码',
  viewSwitch: '显示模式',
  edit: '编辑',
  editFile: '编辑文件',
  save: '保存编辑',
  editor: '编辑正文',
  leaveEditing: '取消编辑',
  retry: '重试',
  loadFailed: '文件加载失败',
  saveFailed: '保存失败',
  unsupported: '不支持预览此格式',
  loading: '加载中…',
  download: '下载',
  fileEmpty: '该文件为空',
  noContent: '暂无可显示内容',
  truncated: '仅显示部分内容',
} satisfies Required<FilePreviewLabels>;

export default messages;
