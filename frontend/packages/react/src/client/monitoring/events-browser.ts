import type { Spec } from './event-spec.js';
import type {
  OperationFault,
  DirectoryReady,
  PreviewReady,
  SearchReady,
  DashboardReady,
  TrajectoryReady,
} from './views.js';
export type BrowserEvents = {
  readonly file_browser_directory_load_ready: Spec<
    'FileBrowser',
    DirectoryReady,
    'directory'
  >;
  readonly file_browser_directory_load_fail: Spec<
    'FileBrowser',
    OperationFault,
    'directory'
  >;
  readonly file_browser_preview_load_ready: Spec<
    'FileBrowser',
    PreviewReady,
    'preview'
  >;
  readonly file_browser_preview_load_fail: Spec<
    'FileBrowser',
    OperationFault,
    'preview'
  >;
  readonly file_browser_search_find_ready: Spec<
    'FileBrowser',
    SearchReady,
    'search'
  >;
  readonly file_browser_search_find_fail: Spec<
    'FileBrowser',
    OperationFault,
    'search'
  >;
  readonly file_browser_search_query_ready: Spec<
    'FileBrowser',
    SearchReady,
    'search'
  >;
  readonly file_browser_search_query_fail: Spec<
    'FileBrowser',
    OperationFault,
    'search'
  >;
  readonly file_browser_search_grep_ready: Spec<
    'FileBrowser',
    SearchReady,
    'search'
  >;
  readonly file_browser_search_grep_fail: Spec<
    'FileBrowser',
    OperationFault,
    'search'
  >;
  readonly file_browser_search_glob_ready: Spec<
    'FileBrowser',
    SearchReady,
    'search'
  >;
  readonly file_browser_search_glob_fail: Spec<
    'FileBrowser',
    OperationFault,
    'search'
  >;
  readonly file_browser_wiki_dashboard_load_ready: Spec<
    'FileBrowser',
    DashboardReady,
    'wiki-dashboard'
  >;
  readonly file_browser_wiki_dashboard_load_fail: Spec<
    'FileBrowser',
    OperationFault,
    'wiki-dashboard'
  >;
  readonly file_browser_experience_dashboard_load_ready: Spec<
    'FileBrowser',
    DashboardReady,
    'experience-dashboard'
  >;
  readonly file_browser_experience_dashboard_load_fail: Spec<
    'FileBrowser',
    OperationFault,
    'experience-dashboard'
  >;
  readonly file_browser_experience_trajectory_load_ready: Spec<
    'FileBrowser',
    TrajectoryReady,
    'trajectory'
  >;
  readonly file_browser_experience_trajectory_load_fail: Spec<
    'FileBrowser',
    OperationFault,
    'trajectory'
  >;
};
