/**
 * OpenViking 原生接口的路径。集中在这里，接口改名时只改一处；调用方不要自己
 * 拼字符串。路径不含服务地址与网关前缀，拼接由 Client 完成。
 *
 * 用 `as const` 而不是 `enum`：字符串 enum 在 `isolatedModules` 下不会内联，
 * 生成的对象还会被当成副作用；作为公开导出时它是 ambient const enum，消费者
 * 一旦开启 `isolatedModules` 或 `verbatimModuleSyntax` 就无法使用。
 *
 * Paths of the native OpenViking API. Keeping them here means a rename is one
 * edit, and callers never build strings themselves. A path carries no service
 * address or gateway prefix; the Client joins those.
 *
 * `as const` rather than `enum`: a string enum is not inlined under
 * `isolatedModules`, its emitted object counts as a side effect, and as a public
 * export it is an ambient const enum that consumers with `isolatedModules` or
 * `verbatimModuleSyntax` cannot use.
 */
export const OV_PATHS = {
  /** 服务健康探针。Service health probe. */
  health: '/health',
  systemStatus: '/api/v1/system/status',
  /** 资源访问控制。Resource access control. */
  acl: '/api/v1/acl',
  resources: '/api/v1/resources',
  skills: '/api/v1/skills',
  /** 列出目录内容。List directory contents. */
  fsLs: '/api/v1/fs/ls',
  fsAttrs: '/api/v1/fs/attrs',
  compile: '/api/v1/compile',
  compileConfigs: '/api/v1/compile/configs',
  compileConfig: '/api/v1/compile/config',
  compileConfigUpdate: '/api/v1/compile/config/update',
  snapshotLog: '/api/v1/snapshot/log',
  snapshotVersions: '/api/v1/snapshot/versions',
  snapshotShow: '/api/v1/snapshot/show',
  snapshotRestore: '/api/v1/snapshot/restore',
  resourceTempUpload: '/api/v1/resources/temp_upload',
  packImport: '/api/v1/pack/import',
  packExport: '/api/v1/pack/export',
  experienceTrajectories: '/api/v1/agent-evolution/experiences/trajectories',
  experienceOutcomes: '/api/v1/agent-evolution/experiences/outcomes',
  /** 读取条目状态。Read an entry's status. */
  fsStat: '/api/v1/fs/stat',
  /** 读取目录树。Read a directory tree. */
  fsTree: '/api/v1/fs/tree',
  /** 新建目录。Create a directory. */
  fsMkdir: '/api/v1/fs/mkdir',
  /** 移动或重命名。Move or rename. */
  fsMv: '/api/v1/fs/mv',
  fsCp: '/api/v1/fs/cp',
  /** 删除文件或目录。Remove a file or directory. */
  fsRm: '/api/v1/fs',
  /** 读取文件正文。Read file content. */
  contentRead: '/api/v1/content/read',
  /** 读取目录的 L0 摘要。Read a directory's L0 abstract. */
  contentAbstract: '/api/v1/content/abstract',
  /** 读取目录的 L1 概览。Read a directory's L1 overview. */
  contentOverview: '/api/v1/content/overview',
  /** 下载文件原始字节。Download a file's raw bytes. */
  contentDownload: '/api/v1/content/download',
  /** 写入文件正文。Write file content. */
  contentWrite: '/api/v1/content/write',
  /** 按模式匹配文件。Match files by pattern. */
  searchGlob: '/api/v1/search/glob',
  /** 语义检索。Semantic search. */
  searchFind: '/api/v1/search/find',
  /** 携带上下文的语义检索。Semantic search with session context. */
  searchQuery: '/api/v1/search/search',
  /** 按正则检索正文。Search content by regular expression. */
  searchGrep: '/api/v1/search/grep',
  /** 会话列表与新建。Session list and creation. */
  sessions: '/api/v1/sessions',
  tasks: '/api/v1/tasks',
  /** 非流式对话。Non-streaming chat. */
  botChat: '/bot/v1/chat',
  /** 流式对话。Streaming chat. */
  botChatStream: '/bot/v1/chat/stream',
} as const;

/** 一个静态路径的取值类型。Value type of one static path. */
export type OvPath = (typeof OV_PATHS)[keyof typeof OV_PATHS];

/**
 * 带路径参数的接口。参数会被 URL 编码：会话 id 里出现 `/` 时不能当成路径分隔。
 * Endpoints with a path parameter. The value is URL-encoded, so a session id
 * containing `/` is not read as a path separator.
 */
export const OV_PATH_BUILDERS = {
  /** 账号用户目录（可关闭凭证返回）。Account user directory. */
  accountUsers: (accountId: string) =>
    `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/users`,
  /** 账号用户组目录。Account group directory. */
  accountGroups: (accountId: string) =>
    `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/groups`,
  /** 单个会话。One session. */
  session: (sessionId: string) =>
    `${OV_PATHS.sessions}/${encodeURIComponent(sessionId)}`,
  /** 会话的消息。Messages of one session. */
  sessionMessages: (sessionId: string) =>
    `${OV_PATH_BUILDERS.session(sessionId)}/messages`,
  sessionCommit: (sessionId: string) =>
    `${OV_PATH_BUILDERS.session(sessionId)}/commit`,
  task: (taskId: string) => `${OV_PATHS.tasks}/${encodeURIComponent(taskId)}`,
  taskCancel: (taskId: string) => `${OV_PATH_BUILDERS.task(taskId)}/cancel`,
} as const;
