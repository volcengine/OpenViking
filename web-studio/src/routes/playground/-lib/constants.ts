import type { TerminalCommandSuggestion } from './types'

export const ROOT_URI = 'viking://'
export const PLAYGROUND_LEFT_WIDTH_STORAGE_KEY =
  'openviking.playground.leftWidth'
export const PLAYGROUND_RIGHT_WIDTH_STORAGE_KEY =
  'openviking.playground.rightWidth'
export const PLAYGROUND_AGENT_SESSIONS_STORAGE_KEY =
  'openviking.playground.agentSessions'
export const PLAYGROUND_LEFT_WIDTH = {
  default: 330,
  max: 620,
  min: 240,
}
export const PLAYGROUND_RIGHT_WIDTH = {
  default: 430,
  max: 680,
  min: 320,
}
export const PLAYGROUND_MAIN_MIN_WIDTH = 420

export const TERMINAL_COMMANDS: TerminalCommandSuggestion[] = [
  {
    command: '/status',
    executable: true,
    key: 'status',
    insertText: '/status',
  },
  { command: '/health', key: 'health', insertText: '/health' },
  { command: '/version', key: 'version', insertText: '/version' },
  { adminOnly: true, command: '/wait', key: 'wait', insertText: '/wait ' },

  { command: '/ls', executable: true, key: 'ls', insertText: '/ls ' },
  { command: '/tree', key: 'tree', insertText: '/tree ' },
  { command: '/mkdir', key: 'mkdir', insertText: '/mkdir ' },
  { command: '/rm', key: 'rm', insertText: '/rm ' },
  { command: '/mv', key: 'mv', insertText: '/mv ' },
  { command: '/stat', key: 'stat', insertText: '/stat ' },
  { command: '/read', executable: true, key: 'read', insertText: '/read ' },
  { command: '/abstract', key: 'abstract', insertText: '/abstract ' },
  { command: '/overview', key: 'overview', insertText: '/overview ' },
  { command: '/write', key: 'write', insertText: '/write ' },
  { command: '/get', key: 'get', insertText: '/get ' },
  { command: '/find', executable: true, key: 'find', insertText: '/find ' },
  {
    command: '/search',
    executable: true,
    key: 'search',
    insertText: '/search ',
  },
  { command: '/grep', key: 'grep', insertText: '/grep ' },
  { command: '/glob', key: 'glob', insertText: '/glob ' },
  { command: '/relations', key: 'relations', insertText: '/relations ' },
  { command: '/link', key: 'link', insertText: '/link ' },
  { command: '/unlink', key: 'unlink', insertText: '/unlink ' },
  { command: '/export', key: 'export', insertText: '/export ' },
  {
    adminOnly: true,
    command: '/backup',
    key: 'backup',
    insertText: '/backup ',
  },
  { command: '/import', key: 'import', insertText: '/import ' },
  {
    adminOnly: true,
    command: '/restore',
    key: 'restore',
    insertText: '/restore ',
  },
  {
    command: '/add-resource',
    executable: true,
    key: 'addResource',
    insertText: '/add-resource',
  },
  { command: '/add-skill', key: 'addSkill', insertText: '/add-skill ' },
  { command: '/add-memory', key: 'addMemory', insertText: '/add-memory ' },

  { command: '/session new', key: 'sessionNew', insertText: '/session new' },
  { command: '/session list', key: 'sessionList', insertText: '/session list' },
  { command: '/session get', key: 'sessionGet', insertText: '/session get ' },
  {
    command: '/session get-session-context',
    key: 'sessionGetSessionContext',
    insertText: '/session get-session-context ',
  },
  {
    command: '/session get-session-archive',
    key: 'sessionGetSessionArchive',
    insertText: '/session get-session-archive ',
  },
  {
    command: '/session delete',
    key: 'sessionDelete',
    insertText: '/session delete ',
  },
  {
    command: '/session add-message',
    key: 'sessionAddMessage',
    insertText: '/session add-message ',
  },
  {
    command: '/session add-messages',
    key: 'sessionAddMessages',
    insertText: '/session add-messages ',
  },
  {
    command: '/session commit',
    key: 'sessionCommit',
    insertText: '/session commit ',
  },

  {
    adminOnly: true,
    command: '/privacy categories',
    key: 'privacyCategories',
    insertText: '/privacy categories',
  },
  {
    adminOnly: true,
    command: '/privacy list',
    key: 'privacyList',
    insertText: '/privacy list ',
  },
  {
    adminOnly: true,
    command: '/privacy get',
    key: 'privacyGet',
    insertText: '/privacy get ',
  },
  {
    adminOnly: true,
    command: '/privacy upsert',
    key: 'privacyUpsert',
    insertText: '/privacy upsert ',
  },
  {
    adminOnly: true,
    command: '/privacy versions',
    key: 'privacyVersions',
    insertText: '/privacy versions ',
  },
  {
    adminOnly: true,
    command: '/privacy version',
    key: 'privacyVersion',
    insertText: '/privacy version ',
  },
  {
    adminOnly: true,
    command: '/privacy activate',
    key: 'privacyActivate',
    insertText: '/privacy activate ',
  },

  {
    adminOnly: true,
    command: '/task status',
    key: 'taskStatus',
    insertText: '/task status ',
  },
  {
    adminOnly: true,
    command: '/task list',
    key: 'taskList',
    insertText: '/task list',
  },
  {
    adminOnly: true,
    command: '/task watch ls',
    key: 'taskWatchLs',
    insertText: '/task watch ls',
  },
  {
    adminOnly: true,
    command: '/task watch show',
    key: 'taskWatchShow',
    insertText: '/task watch show ',
  },
  {
    adminOnly: true,
    command: '/task watch rm',
    key: 'taskWatchRm',
    insertText: '/task watch rm ',
  },
  {
    adminOnly: true,
    command: '/task watch pause',
    key: 'taskWatchPause',
    insertText: '/task watch pause ',
  },
  {
    adminOnly: true,
    command: '/task watch resume',
    key: 'taskWatchResume',
    insertText: '/task watch resume ',
  },
  {
    adminOnly: true,
    command: '/task watch update',
    key: 'taskWatchUpdate',
    insertText: '/task watch update ',
  },
  {
    adminOnly: true,
    command: '/task watch trigger',
    key: 'taskWatchTrigger',
    insertText: '/task watch trigger ',
  },

  {
    adminOnly: true,
    command: '/observer queue',
    key: 'observerQueue',
    insertText: '/observer queue',
  },
  {
    adminOnly: true,
    command: '/observer vikingdb',
    key: 'observerVikingdb',
    insertText: '/observer vikingdb',
  },
  {
    adminOnly: true,
    command: '/observer models',
    key: 'observerModels',
    insertText: '/observer models',
  },
  {
    adminOnly: true,
    command: '/observer transaction',
    key: 'observerTransaction',
    insertText: '/observer transaction',
  },
  {
    adminOnly: true,
    command: '/observer retrieval',
    key: 'observerRetrieval',
    insertText: '/observer retrieval',
  },
  {
    adminOnly: true,
    command: '/observer filesystem',
    key: 'observerFilesystem',
    insertText: '/observer filesystem',
  },
  {
    adminOnly: true,
    command: '/observer system',
    key: 'observerSystem',
    insertText: '/observer system',
  },

  { command: '/config', key: 'config', insertText: '/config' },
  { command: '/config show', key: 'configShow', insertText: '/config show' },
  {
    command: '/config validate',
    key: 'configValidate',
    insertText: '/config validate',
  },
  {
    command: '/config switch',
    key: 'configSwitch',
    insertText: '/config switch',
  },
  { command: '/language', key: 'language', insertText: '/language ' },

  {
    adminOnly: true,
    command: '/admin create-account',
    key: 'adminCreateAccount',
    insertText: '/admin create-account ',
  },
  {
    adminOnly: true,
    command: '/admin list-accounts',
    key: 'adminListAccounts',
    insertText: '/admin list-accounts',
  },
  {
    adminOnly: true,
    command: '/admin delete-account',
    key: 'adminDeleteAccount',
    insertText: '/admin delete-account ',
  },
  {
    adminOnly: true,
    command: '/admin register-user',
    key: 'adminRegisterUser',
    insertText: '/admin register-user ',
  },
  {
    adminOnly: true,
    command: '/admin list-users',
    key: 'adminListUsers',
    insertText: '/admin list-users ',
  },
  {
    adminOnly: true,
    command: '/admin list-agents',
    key: 'adminListAgents',
    insertText: '/admin list-agents ',
  },
  {
    adminOnly: true,
    command: '/admin remove-user',
    key: 'adminRemoveUser',
    insertText: '/admin remove-user ',
  },
  {
    adminOnly: true,
    command: '/admin set-role',
    key: 'adminSetRole',
    insertText: '/admin set-role ',
  },
  {
    adminOnly: true,
    command: '/admin regenerate-key',
    key: 'adminRegenerateKey',
    insertText: '/admin regenerate-key ',
  },

  {
    adminOnly: true,
    command: '/system wait',
    key: 'systemWait',
    insertText: '/system wait ',
  },
  {
    adminOnly: true,
    command: '/system status',
    key: 'systemStatus',
    insertText: '/system status',
  },
  {
    adminOnly: true,
    command: '/system health',
    key: 'systemHealth',
    insertText: '/system health',
  },
  {
    adminOnly: true,
    command: '/system consistency',
    key: 'systemConsistency',
    insertText: '/system consistency ',
  },
  {
    adminOnly: true,
    command: '/system crypto init-key',
    key: 'systemCryptoInitKey',
    insertText: '/system crypto init-key ',
  },
  {
    adminOnly: true,
    command: '/reindex',
    key: 'reindex',
    insertText: '/reindex ',
  },

  { command: '/tui', key: 'tui', insertText: '/tui ' },
  { command: '/chat', key: 'chat', insertText: '/chat ' },
]
