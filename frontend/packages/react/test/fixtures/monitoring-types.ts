import type { ComponentProps } from 'react';
import { OpenVikingProvider } from '@openviking/react/client';
import type {
  OpenVikingMonitoringEvent,
  OpenVikingMonitoringEventName,
  OpenVikingMonitoringEventMap,
  OpenVikingMonitoringOptions,
} from '@openviking/react/client';
import { FilePreview } from '@openviking/react/components/file-preview';
import { Console } from '@openviking/react/modules/console';
const names = [
  'context_picker_render_error',
  'file_browser_action_bar_render_error',
  'file_tree_render_error',
  'file_preview_render_error',
  'command_console_render_error',
  'scope_switcher_render_error',
  'scope_selector_render_error',
  'agent_connect_render_error',
  'viking_bot_render_error',
  'directory_tour_render_error',
  'wiki_dashboard_render_error',
  'experience_dashboard_render_error',
  'email_login_form_render_error',
  'api_key_login_form_render_error',
  'password_reset_request_form_render_error',
  'password_setup_form_render_error',
  'login_method_selector_render_error',
  'invitation_confirmation_render_error',
  'file_browser_render_error',
  'console_render_error',
  'scope_cascade_render_error',
  'file_preview_viewer_load_ready',
  'file_preview_viewer_load_fail',
  'file_preview_editor_load_ready',
  'file_preview_editor_load_fail',
  'viking_bot_composer_load_ready',
  'viking_bot_composer_load_fail',
  'viking_bot_message_list_load_ready',
  'viking_bot_message_list_load_fail',
  'file_preview_image_load_ready',
  'file_preview_image_load_fail',
  'file_preview_video_metadata_load_ready',
  'file_preview_video_metadata_load_fail',
  'file_preview_image_dialog_ready',
  'file_preview_image_dialog_fail',
  'client_request_success',
  'client_request_fail',
  'client_stream_connect_ready',
  'client_stream_connect_fail',
  'client_stream_read_complete',
  'client_stream_read_fail',
  'task_poll_request_fail',
  'task_poll_protocol_fail',
  'task_poll_recovered',
  'file_browser_directory_load_ready',
  'file_browser_directory_load_fail',
  'file_browser_preview_load_ready',
  'file_browser_preview_load_fail',
  'file_browser_search_find_ready',
  'file_browser_search_find_fail',
  'file_browser_search_query_ready',
  'file_browser_search_query_fail',
  'file_browser_search_grep_ready',
  'file_browser_search_grep_fail',
  'file_browser_search_glob_ready',
  'file_browser_search_glob_fail',
  'file_browser_wiki_dashboard_load_ready',
  'file_browser_wiki_dashboard_load_fail',
  'file_browser_experience_dashboard_load_ready',
  'file_browser_experience_dashboard_load_fail',
  'file_browser_experience_trajectory_load_ready',
  'file_browser_experience_trajectory_load_fail',
  'console_session_list_load_ready',
  'console_session_list_load_fail',
  'console_session_create_success',
  'console_session_create_fail',
  'console_session_history_load_ready',
  'console_session_history_load_fail',
  'console_session_delete_success',
  'console_session_delete_fail',
  'scope_cascade_libraries_load_ready',
  'scope_cascade_libraries_load_fail',
  'scope_cascade_spaces_load_ready',
  'scope_cascade_spaces_load_fail',
  'scope_cascade_users_load_ready',
  'scope_cascade_users_load_fail',
  'file_preview_content_prepare_ready',
  'file_preview_content_prepare_fail',
  'console_chat_first_content_ready',
  'console_chat_reply_complete',
  'console_chat_reply_fail',
  'console_chat_history_save_fail',
] as const satisfies readonly OpenVikingMonitoringEventName[];
type Assert<T extends true> = T;
type Complete = Assert<
  Exclude<OpenVikingMonitoringEventName, (typeof names)[number]> extends never
    ? true
    : false
>;
type PureView = Assert<
  'monitoring' extends keyof ComponentProps<typeof FilePreview> ? false : true
>;
type PureBlock = Assert<
  'monitoring' extends keyof ComponentProps<typeof Console> ? false : true
>;
const off: ComponentProps<typeof OpenVikingProvider> = { monitoring: false };
const config: OpenVikingMonitoringOptions = {
  events: ['client_request_fail'],
  onEvent(event) {
    if (event.name === 'client_request_fail') {
      const method:
        'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS' =
        event.params.method;
      // @ts-expect-error Request failures do not have directory result counts.
      void event.params.entryCount;
    }
    if (event.name === 'file_browser_directory_load_fail') {
      const owner: 'FileBrowser' = event.source.component.name;
      const region: 'directory' = event.source.component.region;
    }
  },
};
// @ts-expect-error Unknown monitoring names are rejected.
const invalid: OpenVikingMonitoringEventName = 'copy_everything';
// @ts-expect-error A receiver is required when enabled.
const incomplete: OpenVikingMonitoringOptions = { events: [] };
const asyncDiagnostics: OpenVikingMonitoringOptions['diagnostics'] = {
  // @ts-expect-error Async diagnostic transforms are rejected.
  transform: async () => ({}),
};
type RequestFault =
  OpenVikingMonitoringEventMap['client_request_fail']['params'];
declare const base: RequestFault;
// @ts-expect-error Error codes must match the error kind.
const wrongCode: RequestFault = {
  ...base,
  errorKind: 'network',
  code: 'HTTP_ERROR',
};
// @ts-expect-error Source owner and event name cannot be mixed.
const wrongOwner: OpenVikingMonitoringEventMap['file_browser_directory_load_fail']['source']['component']['name'] =
  'Console';
