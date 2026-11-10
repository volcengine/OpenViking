import type { FileTreeInfoLabels } from '../../components/file-tree/index.js';

/**
 * 目录说明与浮层字段名的中文文案。
 *
 * 组件内置的是英文，模块这一层必须按当前语言整份传下去——只覆盖其中几项会让
 * 同一张卡片里中英文混排，比全英文更难读。
 *
 * Chinese copy for the directory descriptions and the field names inside the
 * popover.
 *
 * The component ships English; the module passes a whole set per language. A
 * partial override would mix Chinese and English inside one card, which reads worse
 * than English alone.
 */
export const TREE_INFO_LABELS_ZH_CN: FileTreeInfoLabels = {
  description: {
    userRoot: '当前用户的上下文数据',
    memories: '跨会话沉淀的长期记忆',
    peers: '按应用或工作区隔离的上下文',
    privacy: '密钥等敏感配置',
    userResources: '该用户的私有知识资料',
    sessions: '对话与任务运行记录',
    skills: 'Agent 可用的技能与工具',
    profile: '用户身份与背景',
    preferences: '个性化偏好',
    entities: '人物、项目、组织等实体',
    events: '按日期归档的关键事件',
    identity: 'Agent 名称与人设',
    soul: 'Agent 行为准则与边界',
    cases: '用于训练经验的交互案例',
    trajectories: '可复用的任务执行轨迹',
    experiences: '自动提炼的执行经验',
    sharedResources: 'Agent 可引用的外部资料',
    agentRoot: 'Agent 的能力、工具与经验',
    agentEndpoints: '账号内共享的通信端点配置',
    agentPayments: '账号内共享的支付能力配置',
    agentSkills: '账号内可调用的技能定义',
    agentTools: '账号内共享的工具配置',
    memoryRoot: '长期记忆与累积信息',
    resourceRoot: 'Agent 可引用的外部知识',
    sessionRoot: '用户的原始会话记录',
    systemRoot: '系统内置资源',
  },
  triggerBehavior: {
    userRoot: '根据当前用户身份自动建立，无需手动创建',
    memories: '提交会话、主动要求记住信息，或从会话中持续抽取',
    peers: '以特定 peer 身份连接时建立，按 peer_id 隔离记忆与资料',
    userResources: '导入资料时上传到该用户的私有资源目录',
    sessions: '会话调用 OpenViking 时建立，消息、工具调用与归档持续写入',
    skills: '作为个人技能被创建、安装或导入',
    profile: '对话中出现稳定、明确的用户背景或工作方式',
    preferences: '用户明确表达某个偏好、习惯或风格',
    entities: '对话中出现值得保留的人物、项目或组织',
    events: '对话中出现重要决定、承诺、安排、进展或结果',
    identity: '首次记忆抽取时初始化，之后由写入操作更新',
    soul: '首次记忆抽取时初始化，之后由写入操作调整',
    cases: '提交了任务明确、执行结果可评估的会话',
    trajectories: '提交了包含决策、工具调用或多步执行的任务',
    experiences: '从任务案例与执行轨迹中提炼',
    sharedResources: '导入资料时上传到共享资源空间',
    agentEndpoints: '为账号注册集成端点时配置',
    agentPayments: '为账号开通支付能力时配置',
    agentSkills: '作为共享技能安装或导入，供所有 Agent 使用',
    agentTools: '为账号注册工具时配置',
  },
  field: {
    description: '说明',
    triggerBehavior: '触发行为',
    path: '路径',
  },
};
