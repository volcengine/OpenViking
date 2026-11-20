const enUS = {
  data: 'SKILL.md content, structured Skill JSON, or a remote Skill URL. Provide either this input or --temp-file-id.',
  'resource-source':
    'Local path or remote URL. Provide either this input or --temp-file-id.',
  'temp-file-id':
    'ID returned by the temporary upload API. Use it in place of the source argument.',
  'resource-instruction': 'Instructions for processing the imported source',
  'ignore-dirs': 'Comma-separated names of directories to skip',
  include: 'Comma-separated file patterns to include',
  exclude: 'Comma-separated file patterns to exclude',
  'watch-interval': 'Refresh interval in minutes. Zero disables monitoring.',
  'resource-acl':
    'Resource access settings as a JSON object with acl_mode and entries',
  telemetry: 'Request server telemetry, for example {"summary":true}',
};

const zhCN = {
  data: 'SKILL.md 原文、结构化 Skill JSON 或远程 Skill URL。与 --temp-file-id 二选一。',
  'resource-source': '要导入的本地路径或远程 URL。与 --temp-file-id 二选一。',
  'temp-file-id': '临时上传接口返回的文件 ID，用于替代来源参数。',
  'resource-instruction': '导入时附加的处理说明',
  'ignore-dirs': '忽略的目录名，使用英文逗号分隔',
  include: '包含的文件匹配模式，使用英文逗号分隔',
  exclude: '排除的文件匹配模式，使用英文逗号分隔',
  'watch-interval': '自动刷新间隔，单位分钟。0 表示不监控。',
  'resource-acl': '资源 ACL 配置 JSON，包含 acl_mode 和 entries',
  telemetry: '请求服务端遥测，例如 {"summary":true}',
} satisfies Record<keyof typeof enUS, string>;

export const coreParameters = { 'en-US': enUS, 'zh-CN': zhCN };
