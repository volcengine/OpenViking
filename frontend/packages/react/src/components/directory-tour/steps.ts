import { resolveLocale, type Locale } from '../../shared/locale.js';
import type { DirectoryTourStep, DirectoryTourTarget } from './types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const targets = [
  { id: 'memories', target: 'userMemories' },
  { id: 'sessions', target: 'userSessions' },
  { id: 'personal-resources', target: 'userResources' },
  { id: 'shared-resources', target: 'sharedResources' },
  { id: 'experiences', target: 'userExperiences' },
  { id: 'peers', target: 'userPeers' },
] as const;

type Preset = readonly (DirectoryTourStep & {
  target: DirectoryTourTarget;
})[];

const stepsByLocale: Record<Locale, Preset> = {
  'en-US': targets.map((step) => ({ ...step, ...enUS.steps[step.target] })),
  'zh-CN': targets.map((step) => ({ ...step, ...zhCN.steps[step.target] })),
};

/** Six standard directory steps, with localized titles and descriptions. */
export function getOpenVikingDirectoryTourSteps(
  locale: Locale = 'en-US',
): Preset {
  return resolveLocale(stepsByLocale, locale);
}

/** English preset retained for consumers that provide their own step content. */
export const openVikingDirectoryTourSteps =
  getOpenVikingDirectoryTourSteps('en-US');
