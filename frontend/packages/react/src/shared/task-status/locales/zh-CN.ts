import type en from './en-US.js';
export default {
  submitted: '任务已提交',
  pending: '等待执行',
  running: '任务执行中',
  cancelling: '正在取消任务',
  completed: '任务已完成',
  failed: '任务执行失败',
  cancelled: '任务已取消',
  invalidResponse: '任务状态响应无效',
  queryError: '暂时无法查询状态，正在重试',
  kinds: {
    resource: '资源导入',
    skill: 'Skill 导入',
    memory: '记忆生成',
    compile: '上下文编译',
  },
  task: '后台任务',
} satisfies typeof en;
