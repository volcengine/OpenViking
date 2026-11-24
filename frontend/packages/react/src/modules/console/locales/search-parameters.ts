export const SEARCH_PARAMETERS_EN_US = {
  query: 'Enter keywords or a natural-language question, or provide an image',
  image: 'Search by image from an HTTPS URL, a Viking URI, or a data:image URL',
  mode: 'Return ranked results with list, or assemble context with context',
  'search-session-id': 'Optional session ID to associate with context search',
  'search-target-uri':
    'Limit retrieval to one URI or a comma-separated list of URIs. Use list mode for search',
  'node-limit': 'Maximum number of results',
  threshold: 'Minimum relevance score',
  level:
    'Filter by level in ranked results. Use 0 for abstract, 1 for overview, and 2 for file',
  after: 'Only include results after this time',
  before: 'Only include results before this time',
  'events-time-decay-protection':
    'Grace period before event memories decay, for example 30m, 24h, or 7d. Use 0 for immediate decay',
  'search-root-uri': 'Root URI to search',
  'grep-pattern': 'Text or regular expression to match',
  'glob-pattern': 'Glob pattern to match, such as *.md',
  'ignore-case': 'Match without regard to case',
  'exclude-uri': 'Skip matches under this URI prefix',
  'level-limit': 'Maximum directory depth',
};

export const SEARCH_PARAMETERS_ZH_CN: Record<
  keyof typeof SEARCH_PARAMETERS_EN_US,
  string
> = {
  query: '输入检索关键词或自然语言问题，也可以提供图片',
  image: '以图检索，支持 HTTPS URL、Viking URI 和 data:image URL',
  mode: 'list 返回排序后的结果列表，context 返回组装后的上下文',
  'search-session-id': '上下文检索可关联的会话 ID',
  'search-target-uri':
    '限定检索范围，可传单个 URI 或用英文逗号分隔多个 URI。search 仅在 list 模式下支持',
  'node-limit': '最多返回的结果数',
  threshold: '最低相关分数',
  level: '按内容层级筛选排序结果，0 为摘要，1 为概览，2 为文件',
  after: '只包含此时间之后的结果',
  before: '只包含此时间之前的结果',
  'events-time-decay-protection':
    '事件记忆开始衰减前的保护期，可填 30m、24h 或 7d 等，填 0 表示立即衰减',
  'search-root-uri': '搜索根 URI',
  'grep-pattern': '用于匹配的文本或正则表达式',
  'glob-pattern': '用于匹配的 glob 表达式，例如 *.md',
  'ignore-case': '匹配时忽略大小写',
  'exclude-uri': '跳过此 URI 前缀下的匹配',
  'level-limit': '最大目录遍历深度',
};
