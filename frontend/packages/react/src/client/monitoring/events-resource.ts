import type { Spec } from './event-spec.js';
import type { CodeReady, CodeFault, MediaReady, MediaFault } from './views.js';
export type ResourceEvents = {
  readonly file_preview_viewer_load_ready: Spec<
    'FilePreview',
    CodeReady,
    'viewer-loader'
  >;
  readonly file_preview_viewer_load_fail: Spec<
    'FilePreview',
    CodeFault,
    'viewer-loader'
  >;
  readonly file_preview_editor_load_ready: Spec<
    'FilePreview',
    CodeReady,
    'editor-loader'
  >;
  readonly file_preview_editor_load_fail: Spec<
    'FilePreview',
    CodeFault,
    'editor-loader'
  >;
  readonly viking_bot_composer_load_ready: Spec<
    'VikingBot',
    CodeReady,
    'composer'
  >;
  readonly viking_bot_composer_load_fail: Spec<
    'VikingBot',
    CodeFault,
    'composer'
  >;
  readonly viking_bot_message_list_load_ready: Spec<
    'VikingBot',
    CodeReady,
    'message-list'
  >;
  readonly viking_bot_message_list_load_fail: Spec<
    'VikingBot',
    CodeFault,
    'message-list'
  >;
  readonly file_preview_image_load_ready: Spec<
    'FilePreview',
    MediaReady,
    'image'
  >;
  readonly file_preview_image_load_fail: Spec<
    'FilePreview',
    MediaFault,
    'image'
  >;
  readonly file_preview_video_metadata_load_ready: Spec<
    'FilePreview',
    MediaReady,
    'video'
  >;
  readonly file_preview_video_metadata_load_fail: Spec<
    'FilePreview',
    MediaFault,
    'video'
  >;
  readonly file_preview_image_dialog_ready: Spec<
    'FilePreview',
    MediaReady,
    'image-dialog'
  >;
  readonly file_preview_image_dialog_fail: Spec<
    'FilePreview',
    MediaFault,
    'image-dialog'
  >;
};
