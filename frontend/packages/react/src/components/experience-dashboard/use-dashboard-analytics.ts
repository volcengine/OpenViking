import { useTrack } from '../../shared/analytics/use-track.js';
import type { ExperienceDashboardProps } from './types.js';

/** Each native navigation target has its own event; optional host actions stay optional. */
export function useDashboardAnalytics({
  onOpenUri,
  onPageChange,
}: Pick<ExperienceDashboardProps, 'onOpenUri' | 'onPageChange'>) {
  const track = useTrack();
  const open = (
    key: 'experience' | 'trajectory' | 'source' | 'test_material',
  ) =>
    onOpenUri
      ? (uri: string) => {
          track(`experience_dashboard_${key}_open_click`);
          onOpenUri(uri);
        }
      : undefined;
  return {
    experience: open('experience'),
    trajectory: open('trajectory'),
    source: open('source'),
    test: open('test_material'),
    page: onPageChange
      ? (page: number) => {
          track('experience_dashboard_page_change');
          onPageChange(page);
        }
      : undefined,
  };
}
