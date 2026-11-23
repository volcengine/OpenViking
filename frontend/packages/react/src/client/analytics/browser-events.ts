export const browserEvents = {
  file_browser_show: {
    zh: '文件浏览区块进入可视区域。',
    en: 'The file browser enters the viewport.',
    fields: {},
  },
  file_tree_show: {
    zh: '文件树进入可视区域。',
    en: 'The file tree enters the viewport.',
    fields: {},
  },
  file_browser_search_dialog_show: {
    zh: '搜索弹窗实际可见。',
    en: 'The search dialog becomes visible.',
    fields: {},
  },
  file_browser_search_results_show: {
    zh: '当前查询的非空搜索结果实际可见。',
    en: 'Nonempty results of the current query become visible.',
    fields: {},
  },
  file_browser_search_empty_show: {
    zh: '当前查询的无结果状态实际可见。',
    en: 'The empty result of the current query becomes visible.',
    fields: {},
  },
  file_browser_connect_agent_show: {
    zh: '连接 Agent 按钮实际可见。',
    en: 'The connect-agent action becomes visible.',
    fields: {},
  },
  file_browser_import_resources_show: {
    zh: '默认导入资源按钮实际可见。',
    en: 'The default import-resources action becomes visible.',
    fields: {},
  },
  file_browser_agent_connect_drawer_show: {
    zh: '内置 Agent 接入抽屉实际可见。',
    en: 'The built-in agent connection drawer becomes visible.',
    fields: {},
  },
  file_browser_connect_agent_click: {
    zh: '用户点击连接 Agent 按钮。',
    en: 'The user clicks connect agent.',
    fields: {},
  },
  file_browser_import_resources_click: {
    zh: '用户点击默认导入资源按钮；不代表导入完成。',
    en: 'The user clicks the default import action; this does not imply an import completed.',
    fields: {},
  },
  file_tree_entry_select: {
    zh: '用户选择一个文件或目录；不代表内容加载完成。',
    en: 'The user selects a file or directory; this does not imply its content loaded.',
    fields: {},
  },
  file_tree_directory_expand: {
    zh: '用户展开目录。',
    en: 'The user expands a directory.',
    fields: {},
  },
  file_tree_directory_collapse: {
    zh: '用户折叠目录。',
    en: 'The user collapses a directory.',
    fields: {},
  },
  file_browser_search_result_select: {
    zh: '用户选择一条搜索结果。',
    en: 'The user selects a search result.',
    fields: {},
  },
} as const;
