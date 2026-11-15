import type enUS from './en-US.js';
export default {
  title: '选择文件或目录',
  search: '搜索名称',
  selected: '已选：',
  unselected: '未选择',
  clearSearch: '清空搜索',
  selectedCount: (count: number) => `已选 ${count} 个`,
  cancel: '取消',
  confirm: '确定',
  empty: '没有匹配的文件或目录',
} satisfies typeof enUS;
