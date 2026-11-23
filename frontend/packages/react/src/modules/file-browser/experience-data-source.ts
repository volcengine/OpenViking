import type {
  ExperienceDashboardData,
  ExperienceItem,
  ExperienceSourceGroup,
  ExperienceTrajectoryMessages,
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
 * Custom loaders for the built-in experience board.
 * Keep the callback references stable and pass the signal to the underlying request.
 */
export interface FileBrowserExperienceDataSource {
  /** Resolve to undefined to use the default listing for this URI. */
  list?: (
    request: ExperienceRequest & ExperiencePageRequest,
  ) => Promise<ExperienceListPage | undefined>;
  /** Loads summary metrics for an experience. Omit to show unavailable metrics. */
  effect?: (
    request: ExperienceRequest & ExperienceDateRange,
  ) => Promise<NonNullable<ExperienceDashboardData['effect']>>;
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
  /** Provides paged test materials for a single experience. Omit to hide the Test tab. */
  tests?: (request: ExperienceRequest & ExperiencePageRequest) => Promise<{
    items: NonNullable<ExperienceDashboardData['testMaterials']>;
    total: number;
  }>;
  /** Return update groups newest first. Takes precedence over the flat sources loader. */
  sourceGroups?: (
    request: ExperienceRequest,
  ) => Promise<readonly ExperienceSourceGroup[]>;
  /** Load archived messages for a trajectory URI when its details are opened. */
  trajectoryMessages?: (
    request: ExperienceRequest,
  ) => Promise<ExperienceTrajectoryMessages>;
  sources?: (
    request: ExperienceRequest,
  ) => Promise<readonly ExperienceTrajectory[]>;
}
