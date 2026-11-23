import type { DirectoryTourStepMessages } from './step-messages.js';
import type { DirectoryTourLabels } from '../types.js';

const messages: Required<DirectoryTourLabels> & {
  steps: DirectoryTourStepMessages;
} = {
  steps: {
    userMemories: {
      title: '用户记忆（memories）',
      description:
        'Agent 从对话中自动提炼的长期记忆 —— 你的身份、偏好和做过的决定。',
    },
    userSessions: {
      title: '轨迹记录（sessions）',
      description:
        '即长期记忆，你和 Agent 的每一次对话、跑过的任务都完整保存在这里。追溯 Agent 的判断，可随时回来查看原文。',
    },
    userResources: {
      title: '个人资料（resources）',
      description:
        '你主动导入的仅你可见的资料 —— 文档、笔记、知识库都放在这里。',
    },
    sharedResources: {
      title: '团队公共资料（resources）',
      description:
        '成员均可见的公共知识库 —— 文档、规范、历史项目都放在这里。你的 Agent 回答时站在全团队的经验之上，不用每次从零讲起。',
    },
    userExperiences: {
      title: 'Agent 经验（experiences）',
      description:
        'Agent 把成功完成的任务自动沉淀为经验。下次遇到类似问题，直接复用已有方案，少走弯路。',
    },
    userPeers: {
      title: '对象记忆（peers）',
      description:
        '只属于某个对象的记忆（可以是某个仓库/Agent/同事），会单独存进 peers 子目录，互不干扰；通用的偏好和习惯，则放在 memories。',
    },
  },
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
