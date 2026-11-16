import { ArrowUpRightIcon, FileLinkIcon } from '../../icons/index.js';
import { Tooltip } from '../../shared/primitives/tooltip/tooltip.js';
import { ExperienceCopyAction } from './experience-copy-action.js';
import type {
  ExperienceDashboardData,
  ExperienceDashboardLabels,
} from './types.js';

/**
 * 测试内容的一行。`User:` / `Assistant:` 前缀换成两种颜色，`# ` 开头的小标题
 * 用弱化色——与火山版的渲染一致，其余行原样保留。
 *
 * One line of test content. A `User:` / `Assistant:` prefix splits into two colours
 * and a `# ` heading takes the muted tone, matching the console; every other line
 * stays as it is.
 */
function TestContent({ content }: { content: string }) {
  return (
    <div className="ov-experience-dashboard__test-content">
      {content.split('\n').map((line, index) => {
        const role = line.match(/^(User|Assistant):(.*)$/);
        if (role)
          return (
            <div key={index} className="ov-experience-dashboard__test-role">
              <span className="ov-experience-dashboard__test-role-name">
                {role[1]}:
              </span>
              <span className="ov-experience-dashboard__test-role-content">
                {role[2]}
              </span>
            </div>
          );
        if (line.startsWith('# '))
          return (
            <div key={index} className="ov-experience-dashboard__test-heading">
              {line}
            </div>
          );
        return <div key={index}>{line || ' '}</div>;
      })}
    </div>
  );
}

/**
 * 「测试」页签：每份测试材料一张卡片，卡片头是标题、来源轨迹与任务描述，右上角
 * 是复制整段内容的按钮。
 *
 * The "test" tab: one card per test material, headed by its title, source
 * trajectory and task, with a copy-everything button at the top right.
 */
export function ExperienceTests({
  items,
  startIndex = 0,
  labels: t,
  onOpenUri,
}: {
  items: NonNullable<ExperienceDashboardData['testMaterials']>;
  startIndex?: number;
  labels: ExperienceDashboardLabels;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <div className="ov-experience-dashboard__tests">
      {items.map((item, index) => (
        <article key={item.id} className="ov-experience-dashboard__test-card">
          <header>
            <div className="ov-experience-dashboard__test-card-heading">
              <strong>
                {t.testMessage.replace(
                  '{index}',
                  String(startIndex + index + 1),
                )}
              </strong>
              <div className="ov-experience-dashboard__test-meta">
                <span className="ov-experience-dashboard__test-meta-label">
                  {t.sourceUri}
                </span>
                <FileLinkIcon aria-hidden="true" width={12} height={12} />
                <code title={item.uri}>{item.uri ?? '—'}</code>
                <span className="ov-experience-dashboard__path-actions">
                  <ExperienceCopyAction value={item.uri} labels={t} />
                  <Tooltip content={t.open} placement="top">
                    <button
                      type="button"
                      className="ov-experience-dashboard__icon-button"
                      aria-label={t.open}
                      disabled={!item.uri || !onOpenUri}
                      onClick={() => item.uri && onOpenUri?.(item.uri)}
                    >
                      <ArrowUpRightIcon width={12} height={12} />
                    </button>
                  </Tooltip>
                </span>
              </div>
              {item.taskQuery ? (
                <div className="ov-experience-dashboard__test-meta ov-experience-dashboard__test-meta--task">
                  <span className="ov-experience-dashboard__test-meta-label">
                    {t.task}
                  </span>
                  <Tooltip content={item.taskQuery} placement="top">
                    <span className="ov-experience-dashboard__test-task">
                      {item.taskQuery}
                    </span>
                  </Tooltip>
                </div>
              ) : null}
            </div>
            <ExperienceCopyAction value={item.content} labels={t} labelled />
          </header>
          <TestContent content={item.content} />
        </article>
      ))}
    </div>
  );
}
