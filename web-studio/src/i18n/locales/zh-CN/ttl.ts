export default {
  title: 'TTL · 库 {{account}}',
  rootAction: '根目录 TTL 设置',
  initialPolicy: '初始库 TTL',
  initialHint:
    '仅用于 events 和 sessions，创建后可在设置页调整类型及根目录策略。写入不自动续期。',
  scope: '配置范围',
  scopes: {
    global: '库默认策略',
    user_events: '用户 events 默认策略',
    peer_events: 'Peer events 默认策略',
    sessions: 'Sessions 默认策略',
    directory: 'Events 或 sessions 根目录',
  },
  rootUri: '根目录 URI',
  invalidRoot:
    '请输入 events 或 sessions 根目录 URI，具体 Session、日期目录和文件不支持单独配置。',
  policy: '当前库的覆盖策略',
  modes: {
    server: '使用服务端配置（移除库级覆盖）',
    inherit: '继承类型／库默认策略',
    disabled: '不过期',
    days: '固定保留天数',
    absolute: '绝对到期时间',
    bestPractice: '推荐预设：events 60 天，sessions 30 天',
  },
  days: '保留天数',
  absolute: '到期时间（当前本地时区）',
  scopeHint:
    '优先级：根目录策略 → 类型默认策略 → 库默认策略。仅覆盖 events 和 sessions。',
  applyHint:
    '应用于已有未到期目录和未来新建目录。按天计算时保留原始创建时间，写入不自动续期。已到期或删除的目录不会恢复。到期内容隐藏，物理清理按天执行。',
  effective: '当前有效策略：{{policy}}',
  effectiveUnavailable: '无法读取当前有效策略：{{error}}',
  dayCount_one: '{{count}} 天',
  dayCount_other: '{{count}} 天',
  expiresAt: '到期时间：',
  noExpiry: '不过期',
  unavailable: 'TTL 设置需要库管理员或 Root 管理凭据，请检查连接设置。',
  loading: '正在加载 TTL…',
  loadFailed: '读取 TTL 设置失败',
  retry: '重试',
  save: '保存并应用',
  saving: '正在应用…',
  saved: 'TTL 策略已应用。',
  saveFailed:
    'TTL 未完全应用，配置可能已保存。请使用相同配置重试，完成剩余目录的更新。',
} as const
