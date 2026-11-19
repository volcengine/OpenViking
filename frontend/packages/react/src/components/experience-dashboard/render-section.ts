import type { ReactNode } from 'react';
import type {
  ExperienceDashboardProps,
  ExperienceDashboardSection,
  ExperienceDashboardSectionContext,
  ExperienceDashboardSections,
} from './types.js';

type OpenUri = (uri: string) => void;

export function createSectionRenderer(
  sections: ExperienceDashboardSections | undefined,
  context: ExperienceDashboardSectionContext,
  states: NonNullable<ExperienceDashboardProps['states']>,
  events: Partial<
    Record<'experience' | 'trajectory' | 'source' | 'test', OpenUri>
  >,
) {
  const navigation: Partial<Record<ExperienceDashboardSection, OpenUri>> = {
    list: events.experience,
    trajectories: events.trajectory,
    sources: events.source,
    tests: events.test,
  };
  return (section: ExperienceDashboardSection, content: ReactNode) => {
    const render = sections?.[section];
    return render
      ? render(
          {
            ...context,
            state: states[section],
            onOpenUri: navigation[section] ?? context.onOpenUri,
          },
          content,
        )
      : content;
  };
}
