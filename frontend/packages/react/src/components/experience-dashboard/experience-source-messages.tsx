import Markdown from 'react-markdown';
import { UserIcon, VikingBotIcon, WikiDiscussIcon } from '../../icons/index.js';
import type { Locale } from '../../shared/locale.js';
import { experienceTime } from './experience-time.js';
import type { ExperienceTrajectoryMessages } from './types.js';

import { trajectoryDetailLabels } from './labels.js';

function speaker(
  role: string,
  unknown: string,
  labels: ReturnType<typeof trajectoryDetailLabels>,
) {
  switch (role.trim().toLowerCase()) {
    case 'user':
      return {
        name: labels.user,
        variant: 'user',
        icon: <UserIcon width={14} height={14} />,
      };
    case 'assistant':
    case 'agent':
      return {
        name: labels.agent,
        variant: 'agent',
        icon: <WikiDiscussIcon width={14} height={14} />,
      };
    case 'tool':
    case 'function':
      return {
        name: labels.tool,
        variant: 'tool',
        icon: <VikingBotIcon name="tool" width={14} height={14} />,
      };
    default:
      return {
        name: role || unknown,
        variant: 'unknown',
        icon: <WikiDiscussIcon width={14} height={14} />,
      };
  }
}

export function ExperienceSourceMessages({
  items,
  locale,
  unknown,
}: {
  items: ExperienceTrajectoryMessages['items'];
  locale: Locale;
  unknown: string;
}) {
  const labels = trajectoryDetailLabels(locale);
  return (
    <ol className="ov-experience-trajectory-detail__messages">
      {items.map(({ role, content, createdAt, tools }, index) => {
        const { name, variant, icon } = speaker(role, unknown, labels);
        return (
          <li key={index}>
            <div className="ov-experience-trajectory-detail__role">
              <span
                className="ov-experience-trajectory-detail__role-icon"
                data-role={variant}
                aria-hidden="true"
              >
                {icon}
              </span>
              {name}
            </div>
            <div className="ov-experience-trajectory-detail__bubble">
              {content ? (
                <Markdown skipHtml disallowedElements={['img']}>
                  {content}
                </Markdown>
              ) : null}
              {tools?.map((tool) => (
                <details key={tool.id}>
                  <summary>
                    {tool.name} · {tool.status}
                  </summary>
                  {tool.input === undefined ? null : (
                    <pre>{JSON.stringify(tool.input, null, 2)}</pre>
                  )}
                  {tool.output ? <pre>{tool.output}</pre> : null}
                </details>
              ))}
            </div>
            {createdAt ? (
              <time dateTime={createdAt}>
                {experienceTime(createdAt, locale)}
              </time>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
