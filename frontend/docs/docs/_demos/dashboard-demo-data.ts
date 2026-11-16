import type { WikiFileData } from '@openviking/react/components/wiki-dashboard';
import type { ExperienceDashboardData } from '@openviking/react/components/experience-dashboard';
/** Fixed fictional fixtures in the native API shape; no network or credentials. */
export const DEMO_USER = 'viking://user/alice';
export const DEMO_WIKI = `${DEMO_USER}/resources/handbook`;
export const DEMO_EXPERIENCES = `${DEMO_USER}/memories/experiences`;
export const DEMO_EXPERIENCE = `${DEMO_EXPERIENCES}/release-check.md`;
export const DEMO_TRAJECTORY = `${DEMO_USER}/memories/trajectories/release-check.md`;
export const DEMO_WIKI_FILE = `${DEMO_WIKI}/release.md`;
const record = {
  match_text: '发布需至少两名审批人。',
  problem_description: '发布审批人数存在冲突',
  reason: '两份资料对审批人数的描述不同，需要确认当前有效规则。',
  severity: 'high',
  state: 'unfixed',
  begin_time: 1794268800,
  fix_time: null,
  fix_plan: '确认当前审批规则后更新文档。',
  resolution_basis: '',
} as const;
const fixed = {
  ...record,
  match_text: '发布完成后记录验证证据。',
  problem_description: '验证证据规范已统一',
  reason: '历史规则已由人工确认并统一。',
  severity: 'low',
  state: 'fixed',
  fix_time: 1794272400,
  fix_plan: '已统一证据留存规则。',
  resolution_basis: '人工修复',
} as const;
const content = `# 发布流程\n\n发布需至少两名审批人。\n发布完成后记录验证证据。\n\n<!-- CONFLICTS_CONTENT\n${[record, fixed].map((item) => JSON.stringify(item)).join(',\n')}\n-->`;

const ok = (result: unknown) =>
  new Response(JSON.stringify({ status: 'ok', result }), {
    headers: { 'content-type': 'application/json' },
  });

export function dashboardDemoResponse(url: URL): Response | undefined {
  const uri = url.searchParams.get('uri') ?? '';
  switch (url.pathname) {
    case '/api/v1/compile/configs':
      return ok({
        targets: [
          {
            to: DEMO_WIKI,
            compile_type: 'compile',
            initialized: true,
            conflicts_num: 1,
          },
        ],
      });
    case '/api/v1/fs/attrs':
      return ok({
        uri,
        attrs: {
          tags: uri === DEMO_WIKI ? ['compile_target=true'] : [],
          memory: {
            links:
              uri === DEMO_EXPERIENCE
                ? [
                    {
                      link_type: 'derived_from',
                      to_uri: DEMO_TRAJECTORY,
                      description: '发布检查轨迹',
                    },
                  ]
                : [],
          },
        },
      });
    case '/api/v1/fs/stat':
      if (
        uri === DEMO_WIKI ||
        uri === DEMO_WIKI_FILE ||
        uri === DEMO_EXPERIENCE ||
        uri === DEMO_EXPERIENCES
      )
        return ok({
          uri,
          isDir: uri === DEMO_WIKI || uri === DEMO_EXPERIENCES,
        });
      return undefined;
    case '/api/v1/fs/ls':
      if (uri === DEMO_WIKI)
        return ok([
          { uri: DEMO_WIKI_FILE, isDir: false, tags: ['has_conflict=true'] },
        ]);
      if (uri === DEMO_EXPERIENCES)
        return ok([
          {
            uri: DEMO_EXPERIENCE,
            name: 'release-check.md',
            modTime: '2026-11-09 12:03',
            isDir: false,
          },
        ]);
      return undefined;
    case '/api/v1/content/read':
      if (uri === DEMO_WIKI_FILE) return ok(content);
      if (uri === DEMO_EXPERIENCE)
        return ok(
          '# Release checks\n\nVerify approvals and rollback conditions before release.',
        );
      return undefined;
    case '/api/v1/agent-evolution/experiences/outcomes':
      return ok({
        outcome_distribution: [
          { outcome: 'success', count: 8 },
          { outcome: 'partial', count: 2 },
          { outcome: 'failure', count: 1 },
        ],
      });
    case '/api/v1/agent-evolution/experiences/trajectories':
      return ok({
        items: [
          {
            uri: DEMO_TRAJECTORY,
            name: 'Release verification',
            created_at: '2026-11-09T12:03:00Z',
            description: 'Verify approvals and rollback conditions.',
            outcome: 'success',
          },
        ],
        total: 1,
        has_more: false,
      });
    default:
      return undefined;
  }
}

export const dashboardDemoFetch: typeof fetch = (input) => {
  const url = new URL(
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.href
        : input.url,
    'https://offline.demo',
  );
  return Promise.resolve(dashboardDemoResponse(url) ?? ok([]));
};

export const wikiViewData: WikiFileData = {
  path: DEMO_WIKI_FILE,
  conflicts: [record, fixed].map((item, index) => ({
    id: String(index),
    matchText: item.match_text,
    description: item.problem_description,
    reason: item.reason,
    severity: item.severity,
    state: item.state,
    beginTime: item.begin_time,
    fixTime: item.fix_time,
    fixPlan: item.fix_plan,
    resolutionBasis: item.resolution_basis,
    location: { status: 'missing' },
  })),
};
export const experienceViewData: ExperienceDashboardData = {
  experiences: [
    {
      uri: DEMO_EXPERIENCE,
      name: 'release-check.md',
      modTime: '2026-11-09 12:03',
      isUpdated: true,
    },
  ],
  outcomes: [
    { outcome: 'success', count: 8 },
    { outcome: 'partial', count: 2 },
    { outcome: 'failure', count: 1 },
  ],
  trajectories: [
    {
      uri: DEMO_TRAJECTORY,
      name: 'Release verification',
      created_at: '2026-11-09T12:03:00Z',
      description: 'Verify approvals and rollback conditions.',
      outcome: 'success',
    },
  ],
  trajectoryTotal: 1,
  sources: [{ uri: DEMO_TRAJECTORY, name: 'Release verification' }],
};
