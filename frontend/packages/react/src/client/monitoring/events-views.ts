import type { Spec } from './event-spec.js';
import type { OperationFault, OptionsReady, ContentReady } from './views.js';
export type ViewsEvents = {
  readonly scope_cascade_libraries_load_ready: Spec<
    'ScopeCascade',
    OptionsReady,
    'libraries'
  >;
  readonly scope_cascade_libraries_load_fail: Spec<
    'ScopeCascade',
    OperationFault,
    'libraries'
  >;
  readonly scope_cascade_spaces_load_ready: Spec<
    'ScopeCascade',
    OptionsReady,
    'spaces'
  >;
  readonly scope_cascade_spaces_load_fail: Spec<
    'ScopeCascade',
    OperationFault,
    'spaces'
  >;
  readonly scope_cascade_users_load_ready: Spec<
    'ScopeCascade',
    OptionsReady,
    'users'
  >;
  readonly scope_cascade_users_load_fail: Spec<
    'ScopeCascade',
    OperationFault,
    'users'
  >;
  readonly file_preview_content_prepare_ready: Spec<
    'FilePreview',
    ContentReady,
    'content' | 'source'
  >;
  readonly file_preview_content_prepare_fail: Spec<
    'FilePreview',
    OperationFault,
    'content' | 'source'
  >;
};
