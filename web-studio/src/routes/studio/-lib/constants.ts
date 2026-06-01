import type { TerminalCommandSuggestion } from './types'

export const ROOT_URI = 'viking://'
export const STUDIO_LEFT_WIDTH_STORAGE_KEY = 'web-studio-playground-left-width'
export const STUDIO_RIGHT_WIDTH_STORAGE_KEY =
  'web-studio-playground-right-width'
export const STUDIO_AGENT_SESSIONS_STORAGE_KEY = 'web-studio-agent-sessions'
export const STUDIO_LEFT_WIDTH = {
  default: 330,
  max: 620,
  min: 240,
}
export const STUDIO_RIGHT_WIDTH = {
  default: 430,
  max: 680,
  min: 320,
}
export const STUDIO_MAIN_MIN_WIDTH = 420

export const TERMINAL_COMMANDS: TerminalCommandSuggestion[] = [
  {
    command: '/status',
    description: '检查 OpenViking API 和根目录',
    insertText: '/status',
    usage: '/status',
  },
  {
    command: '/ls',
    description: '列出当前目录或指定目录',
    insertText: '/ls ',
    usage: '/ls [viking://resources/...]',
  },
  {
    command: '/search',
    description: '在当前上下文范围内语义搜索',
    insertText: '/search ',
    usage: '/search 查询词',
  },
  {
    command: '/find',
    description: '查找相关上下文资源',
    insertText: '/find ',
    usage: '/find 查询词',
  },
  {
    command: '/read',
    description: '读取并打开一个资源文件',
    insertText: '/read ',
    usage: '/read viking://resources/.../file.md',
  },
  {
    command: '/add-resource',
    description: '打开添加资源表单',
    insertText: '/add-resource',
    usage: '/add-resource',
  },
]
