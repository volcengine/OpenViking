export const connectionEvents = {
  scope_switcher_option_select: {
    zh: '用户选择作用域选项，不包含选项标识或名称。',
    en: 'The user selects a scope option; its identifier and name are omitted.',
    fields: {},
  },
  scope_cascade_selection_complete: {
    zh: '用户操作形成有效的三级选择。',
    en: 'A user selection forms a valid three-level scope.',
    fields: {},
  },
  agent_connect_show: {
    zh: 'Agent 接入组件实际可见。',
    en: 'The agent connection component becomes visible.',
    fields: {},
  },
  agent_connect_tab_change: {
    zh: '用户切换接入分类。',
    en: 'The user switches connection categories.',
    fields: {},
  },
  agent_connect_select: {
    zh: '用户选择接入方式，不包含配置。',
    en: 'The user selects a connection method; configuration is omitted.',
    fields: {},
  },
  agent_connect_instructions_copy_show: {
    zh: '接入指南复制按钮实际可见。',
    en: 'The copy button for connection instructions becomes visible.',
    fields: {},
  },
  agent_connect_instructions_copy_click: {
    zh: '用户复制接入指南，不采集复制内容。',
    en: 'The user copies the connection instructions; copied content is omitted.',
    fields: {},
  },
  agent_connect_instructions_copy_success: {
    zh: '接入指南已写入剪贴板。',
    en: 'The connection instructions are written to the clipboard.',
    fields: {},
  },
  agent_connect_instructions_copy_fail: {
    zh: '接入指南复制失败。',
    en: 'Copying the connection instructions fails.',
    fields: {},
  },
  agent_connect_code_block_copy_show: {
    zh: '代码块复制按钮实际可见。',
    en: 'The copy button for code block becomes visible.',
    fields: {},
  },
  agent_connect_code_block_copy_click: {
    zh: '用户复制代码块，不采集复制内容。',
    en: 'The user copies the code block; copied content is omitted.',
    fields: {},
  },
  agent_connect_code_block_copy_success: {
    zh: '代码块已写入剪贴板。',
    en: 'The code block are written to the clipboard.',
    fields: {},
  },
  agent_connect_code_block_copy_fail: {
    zh: '代码块复制失败。',
    en: 'Copying the code block fails.',
    fields: {},
  },
} as const;
