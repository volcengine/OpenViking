import type { InteractionLabels } from '../labels.js';

const messages = {
  answer: '答案',
  title: '待确认',
  submit: '提交',
  cancel: '取消',
  other: '其他',
  submitted: '已提交',
  cancelled: '已取消',
  progress: (index, total) => `${index + 1} / ${total}`,
  scheduleNone: '不启用',
  scheduleDaily: '每天',
  scheduleWeekly: '每周',
  scheduleTime: '时间',
  scheduleWeekday: '星期',
  weekdays: ['周一', '周二', '周三', '周四', '周五', '周六', '周日'],
} satisfies InteractionLabels;

export default messages;
