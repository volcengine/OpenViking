export const ROOT_URI = 'viking://'

// Keep legacy storage keys so the page rename preserves saved layout and sessions.
export const FILESYSTEM_LEFT_WIDTH_STORAGE_KEY =
  'openviking.playground.leftWidth'
export const FILESYSTEM_RIGHT_WIDTH_STORAGE_KEY =
  'openviking.playground.rightWidth'
export const FILESYSTEM_RIGHT_COLLAPSED_STORAGE_KEY =
  'openviking.playground.rightCollapsed'
export const FILESYSTEM_AGENT_SESSIONS_STORAGE_KEY =
  'openviking.playground.agentSessions'
export const FILESYSTEM_EXPANDED_URIS_STORAGE_KEY =
  'openviking.playground.expandedUris'
export const FILESYSTEM_LEFT_WIDTH = {
  default: 352,
  max: 620,
  min: 240,
}
export const FILESYSTEM_RIGHT_WIDTH = {
  default: 360,
  max: 680,
  min: 320,
}
export const FILESYSTEM_MAIN_MIN_WIDTH = 420
