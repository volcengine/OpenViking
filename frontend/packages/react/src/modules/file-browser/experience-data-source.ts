import type {
  ExperienceDashboardData,
  ExperienceItem,
  ExperienceTrajectory,
} from '../../components/experience-dashboard/types.js';

interface ExperienceRequest {
  uri: string;
  signal: AbortSignal;
}

interface ExperienceDateRange {
  /** UTC dates in YYYY-MM-DD form. Both are omitted for all time. */
  startDate?: string;
  endDate?: string;
}

interface ExperiencePageRequest {
  offset: number;
  limit: number;
}

export interface ExperienceListPage {
  items: readonly ExperienceItem[];
  hasMore: boolean;
  total?: number;
}

/**
 * Custom loaders for the built-in experience board. Omitted loaders use OpenViking.
 * Keep the callback references stable and pass the signal to the underlying request.
 */
export interface FileBrowserExperienceDataSource {
  /** Resolve to undefined to use the default listing for this URI. */
  list?: (
    request: ExperienceRequest & ExperiencePageRequest,
  ) => Promise<ExperienceListPage | undefined>;
  outcomes?: (
    request: ExperienceRequest & ExperienceDateRange,
  ) => Promise<NonNullable<ExperienceDashboardData['outcomes']>>;
  trajectories?: (
    request: ExperienceRequest & ExperienceDateRange & ExperiencePageRequest,
  ) => Promise<{
    items: readonly ExperienceTrajectory[];
    hasMore: boolean;
    total: number;
  }>;
  sources?: (
    request: ExperienceRequest,
  ) => Promise<readonly ExperienceTrajectory[]>;
}
