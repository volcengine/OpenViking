import type { Spec } from './event-spec.js';
import type { RenderFault } from './views.js';
export type RenderEvents = {
  readonly context_picker_render_error: Spec<'ContextPicker', RenderFault>;
  readonly file_browser_action_bar_render_error: Spec<
    'FileBrowserActionBar',
    RenderFault
  >;
  readonly file_tree_render_error: Spec<'FileTree', RenderFault>;
  readonly file_preview_render_error: Spec<'FilePreview', RenderFault>;
  readonly command_console_render_error: Spec<'CommandConsole', RenderFault>;
  readonly scope_switcher_render_error: Spec<'ScopeSwitcher', RenderFault>;
  readonly scope_selector_render_error: Spec<'ScopeSelector', RenderFault>;
  readonly agent_connect_render_error: Spec<'AgentConnect', RenderFault>;
  readonly viking_bot_render_error: Spec<'VikingBot', RenderFault>;
  readonly directory_tour_render_error: Spec<'DirectoryTour', RenderFault>;
  readonly wiki_dashboard_render_error: Spec<'WikiDashboard', RenderFault>;
  readonly experience_dashboard_render_error: Spec<
    'ExperienceDashboard',
    RenderFault
  >;
  readonly email_login_form_render_error: Spec<'EmailLoginForm', RenderFault>;
  readonly api_key_login_form_render_error: Spec<
    'ApiKeyLoginForm',
    RenderFault
  >;
  readonly password_reset_request_form_render_error: Spec<
    'PasswordResetRequestForm',
    RenderFault
  >;
  readonly password_setup_form_render_error: Spec<
    'PasswordSetupForm',
    RenderFault
  >;
  readonly login_method_selector_render_error: Spec<
    'LoginMethodSelector',
    RenderFault
  >;
  readonly invitation_confirmation_render_error: Spec<
    'InvitationConfirmation',
    RenderFault
  >;
  readonly file_browser_render_error: Spec<'FileBrowser', RenderFault>;
  readonly console_render_error: Spec<'Console', RenderFault>;
  readonly scope_cascade_render_error: Spec<'ScopeCascade', RenderFault>;
};
