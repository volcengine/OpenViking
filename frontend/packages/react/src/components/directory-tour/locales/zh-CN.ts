import type { DirectoryTourLabels } from '../types.js';

const messages: Required<DirectoryTourLabels> = {
  close: '关闭引导',
  next: '下一步',
  done: '知道了',
  locating: '正在定位目录…',
  unavailable: '当前暂未找到这个目录。',
  retry: '重试',
  skip: '跳过',
  progress: (current: number, total: number) => `${current}/${total}`,
};

export default messages;
