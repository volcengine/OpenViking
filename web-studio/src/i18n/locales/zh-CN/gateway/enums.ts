const enums = {
  protocol: {
    anthropic: 'Anthropic Messages',
    chat: 'Chat Completions',
    responses: 'Responses',
  },
  vendor: {
    generic: '通用',
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    deepseek: 'DeepSeek',
    ark: '火山方舟',
    byteplus: 'BytePlus 方舟（海外站）',
  },
  authMode: {
    managed: '网关保管 API Key',
    passthrough: '客户端自带 API Key',
  },
  kind: {
    user: '新消息',
    continuation: '工具步骤',
    auxiliary: '辅助请求',
    subagent: '子 Agent',
    count: 'Token 计数',
    passthrough: '直接转发',
    capture: '记忆同步',
  },
  health: {
    ok: '已连接',
    degraded: '异常',
    starting: '启动中',
  },
  captureStatus: {
    active: '正常',
    disabled: '已关闭',
    retrying: '重试中',
    paused: '已暂停',
  },
  captureReason: {
    history_changed: '对话历史有改动，后续内容改存到新的 OpenViking 会话',
    continued_after_idle:
      '已保存的一轮对话在停顿后又继续了，后续内容改存到新的 OpenViking 会话',
    history_changed_during_delivery:
      '保存过程中对话历史有改动，后续内容改存到新的 OpenViking 会话',
    manual_reset: '管理员已手动重新同步',
    delivery_recovered: '保存已恢复正常',
    delivery_failed: '保存到 OpenViking 失败',
    invalid_archive_id: 'OpenViking 返回了无法识别的归档',
  },
  recallReason: {
    recalled: '已补充记忆',
    empty: '没有找到相关内容',
    disabled: '召回已关闭或预算已用完',
    recall_timeout: '召回超时',
  },
  toolSkipReason: {
    tools_require_full_history:
      'Responses 请求需回传完整对话，并设置 store: false',
    upstream_tools_disabled: '这个上游关闭了 OpenViking 工具',
    tools_multiple_choices: '请求要求返回多个候选结果（n > 1）',
    tools_structured_output: '请求要求结构化输出',
    tools_non_function: '客户端传入了非函数类型的工具',
    tools_forced_choice: '客户端指定了必须调用的工具',
    deepseek_reasoning_history_required:
      'DeepSeek 开启了思考模式，但之前回复的推理内容补不回来',
    tool_name_collision: '客户端定义了同名工具',
    tools_unavailable: '这段对话开始时无法加载 OpenViking 工具清单',
    tools_not_selected_at_session_start: '这段对话开始时还没有启用工具',
  },
  toolStopReason: {
    token_budget: '工具 Token 预算已用完',
  },
  windowReminder: {
    soft: '已提醒模型尽快开启新窗口',
    hard: '已要求模型立即开启新窗口',
  },
  openviking: {
    unavailable: 'OpenViking 无法访问或响应超时',
    unauthorized: 'OpenViking 拒绝了这个密钥，它可能填错了或已被吊销。',
    httpStatus: 'OpenViking 返回了 HTTP {{status}}',
    errorCode: 'OpenViking 报告了错误 {{code}}',
    versionMismatch: 'OpenViking 版本低于网关的要求',
    identityMissing: 'OpenViking 无法识别这个密钥所属的用户',
    rootKey: '不接受 Root 密钥，请使用本账号下的用户密钥。',
    otherAccount: '这个 OpenViking 密钥属于其他账号。',
  },
  degradation: {
    memory_store_failure: {
      label: '记忆不可用',
      explanation: '网关没能准备记忆，这次请求没有带记忆就发给了模型。',
      action:
        '检查网关所在机器的磁盘和日志。如果刚删除过该用户的网关数据，出现这一项是正常的。',
    },
    unsafe_json: {
      label: '原样转发',
      explanation:
        '请求里有重复的键，或有无法精确重新编码的数字，网关原样转发了它，没有补充记忆。',
      action: '通常由客户端造成，网关这边无需调整。',
    },
    upstream_changed: {
      label: '上游已切换',
      explanation:
        '这段对话最初使用的上游已不可用，改由其他上游回答，模型服务商的缓存也随之重新开始。',
      action:
        '重新启用原来的上游，或让客户端改用包含该上游的密钥；新开的对话会固定使用当前的上游。',
    },
    missing_injection_record: {
      label: '记忆记录缺失',
      explanation:
        '这段对话早先补充的记忆已不在存储中，历史消息只能不带它发出。',
      action:
        '通常发生在存储数据丢失后或很久以前的对话里。如果反复出现，请开始新对话。',
    },
    plugin_present: {
      label: '检测到 OpenViking 插件',
      explanation:
        '客户端已经在使用 OpenViking 插件，网关因此不再为这段对话补充和保存记忆。',
      action:
        '使用插件时出现这一项是正常的。如果想改用网关的记忆，请移除插件后开始新对话。',
    },
    ark_cache_parameters_changed: {
      label: '缓存参数有变化',
      explanation:
        '模型、思考模式、采样参数、系统提示词或工具与对话的第一次请求不同，方舟的提示词缓存很可能没有命中。',
      action: '在同一段对话里保持这些参数不变。',
    },
    hidden_reply_without_anchor: {
      label: '模型未返回回答',
      explanation:
        '模型使用 OpenViking 工具后没有返回可用于继续对话的内容，下一轮无法沿用这次工具调用的结果。',
      action: '重试本次提问，或开始一段新对话。',
    },
    hidden_tool_history_unavailable: {
      label: '工具历史无法继续使用',
      explanation:
        '这段 Claude 对话用过 OpenViking 工具，但当前请求无法使用这些工具。网关已去掉之前的思考内容，继续发送对话文本。',
      action: '查看本次请求的工具停用原因，恢复原设置，或开始一段新对话。',
    },
    hidden_tool_loop_failed: {
      label: 'OpenViking 工具调用失败',
      explanation:
        '模型使用 OpenViking 工具的过程中有一次模型调用失败，客户端收到了错误。',
      action: '确认这个上游支持工具调用，或为它关闭 OpenViking 工具。',
    },
    capture_parse_failure: {
      label: '回复未保存',
      explanation:
        '流式回复无法解析，这条回复没有保存到 OpenViking。客户端不受影响。',
      action: '偶尔出现无需处理；频繁出现时再排查。',
    },
  },
}

export default enums
