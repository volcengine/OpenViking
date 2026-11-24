import type {
  renderErrors as EnglishRenderErrors,
  trajectoryDetail as EnglishTrajectoryDetail,
} from './en-US.js';

export const renderErrors = {
  dashboard: '经验看板显示失败',
} satisfies Record<keyof typeof EnglishRenderErrors, string>;

export const trajectoryDetail = {
  body: '轨迹正文',
  preview: '预览',
  emptyBody: '暂无轨迹正文',
  messages: '来源消息',
  emptyMessages: '暂无来源消息',
  user: '用户',
  agent: '助手',
  tool: '工具',
} satisfies Record<keyof typeof EnglishTrajectoryDetail, string>;

export default renderErrors;
