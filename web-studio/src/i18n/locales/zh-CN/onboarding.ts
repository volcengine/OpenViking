export default {
  directoryOptions: '选择其他数据身份（高级）',
  rootSetupTitle: '开始使用工作台',
  rootSetupDescription:
    'Root Key 用于管理服务，不能直接访问用户数据。当前 Account：{{account}}。创建使用身份后，可在凭据结果中进入工作台；团队成员的身份不会自动替换你的工作台身份。',
  rootSetupCreate: '创建使用身份',
  rootSetupExisting: '配置已有 User Key',
  verifyDirectoryHint:
    '可选。需要额外的管理权限时填写。Account Admin 管理本 Account，Root 管理服务与所有 Account。',
  loadDirectory: '加载 Account 和 User',
  directoryHint: '使用 Root 或 Admin API Key 浏览可选身份。',
  directoryControlRequired:
    '请输入有效的 Root 或 Admin API Key 后加载账号和用户。',
  directoryLoadFailed: '加载身份失败，请检查管理密钥和服务地址后重试。',
  directoryAccount: '选择 Account',
  directoryUser: '选择 User',
  directoryAdminHint:
    'Admin Key 仅可选择所属 Account 中的用户。访问数据还需填写该用户的 API Key。',
  directoryKeyHint:
    '选择身份不会自动授予数据权限。请填写对应的 User API Key；留空则仅进入账号管理。',
  directoryIdentityMismatch:
    '此 User API Key 属于其他账号或用户，请输入所选身份对应的密钥。',

  openSourceConnection: 'OpenSource',
  cancel: '取消',
  exit: '退出 Studio',
  connectionTitle: '连接 OpenViking 服务。',
  preferencesHint: '选择你熟悉的语言与外观。',
  connectionHint: '连接并验证你的 OpenViking 服务。',

  verifiedManagement: '管理权限验证成功。进入用户管理后可创建用户 API 密钥。',
  brand: 'OpenViking Studio',
  invalidService: '这个地址未返回有效的 OpenViking 服务信息。',
  keyRequired: '此服务需要 API Key，请填写后重试。',

  steps: '选择工作区',
  workspace: '选择服务',
  workspaceHint: '选择服务类型与地址，完成首次接入。',
  savedWorkspaces: '已有连接',
  connectionChoice: '选择连接',
  newConnection: '添加新服务地址',
  savedConnectionUnavailable:
    '已保存的连接不可用，请重新选择或输入新的 API Key。',
  newAddressHint: '输入有效的 HTTP 或 HTTPS 地址后可继续。',
  hostedEndpoint: '服务地址',
  preferences: '基础偏好',
  connection: '连接服务',
  welcome: '欢迎使用\nOpenViking Studio',
  welcomeDescription: '完成服务连接与语言、外观设置，即可开始使用。',
  connectionDescription: '配置服务连接。验证成功后，将显示对应的工作区和功能。',
  language: '语言',
  theme: '外观',
  light: '浅色',
  dark: '暗黑',
  system: '跟随系统',
  next: '下一步',
  back: '上一步',
  verify: '验证连接',
  verifying: '正在验证…',
  verified: '连接验证成功',
  verifyFailed: '连接验证失败：',
  healthRequestFailed: '检查服务健康状态时失败：',
  dataRequestFailed: '检查记忆访问权限时失败：',
  enter: '进入 Studio',
  keyLabel: 'API Key',
  keyHelp:
    '可填写 User、Account Admin 或 Root Key，验证后自动识别权限和身份。开发模式无需填写。',
  hostedKeyHelp: '填写火山服务的用户 API Key，以访问当前用户的记忆。',
  advanced: '开源高级连接配置（选填）',
  loadUsers: '读取此账号的用户',
  loadingUsers: '正在读取用户…',
  chooseUser: '选择用户',
  noUsers: '该账号下没有可选用户，请检查账号 ID 或手动填写用户 ID。',
  usersDevMode:
    '当前服务为开发模式，不提供用户列表。请手动填写账号和用户 ID；服务会按这两个值访问对应空间。',
  usersNeedKey:
    '此服务需要 Root 或管理员 API 密钥才能列出用户。填入密钥后再试。',
  usersUnavailable:
    '无法读取用户列表，请检查管理密钥和账号，或手动填写用户 ID。',
  userSelectionHint:
    '填写管理密钥后可读取用户列表；开发模式或无管理权限时，请手动填写账号和用户 ID。API Key 模式下还需要对应用户的密钥。',
  storageError: '无法保存配置，请允许此站点使用浏览器本地存储后重试。',
}
