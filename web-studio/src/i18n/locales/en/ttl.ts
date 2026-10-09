export default {
  title: 'TTL · Library {{account}}',
  rootAction: 'Root TTL settings',
  initialPolicy: 'Initial library TTL',
  initialHint:
    'Applies to events and sessions. You can adjust type and root policies in Settings after creation. Writes do not renew expiry.',
  scope: 'Configuration scope',
  scopes: {
    global: 'Library default',
    user_events: 'User events default',
    peer_events: 'Peer events default',
    sessions: 'Sessions default',
    directory: 'Events or sessions root',
  },
  rootUri: 'Root directory URI',
  invalidRoot:
    'Enter an events or sessions root URI. Individual sessions, dates and files cannot be configured.',
  policy: 'Library override',
  modes: {
    server: 'Use server configuration (remove library override)',
    inherit: 'Inherit type / library default',
    disabled: 'No expiration',
    days: 'Fixed number of days',
    absolute: 'Absolute expiration time',
    bestPractice: 'Recommended defaults: events 60 days, sessions 30 days',
  },
  days: 'Retention days',
  absolute: 'Expiration time (your local time zone)',
  scopeHint:
    'Priority: root policy → type default → library default. Only events and sessions are covered.',
  applyHint:
    'Applies to existing unexpired directories and future new directories. Day-based expiry uses the original creation time; writes do not renew it. Expired or deleted directories are not restored. Expired content is hidden; physical cleanup runs daily.',
  effective: 'Currently effective: {{policy}}',
  effectiveUnavailable:
    'Could not read the current effective policy: {{error}}',
  dayCount_one: '{{count}} day',
  dayCount_other: '{{count}} days',
  expiresAt: 'Expires:',
  noExpiry: 'No expiration',
  unavailable:
    'TTL settings require account administrator or Root credentials. Check connection settings.',
  loading: 'Loading TTL…',
  loadFailed: 'Could not load TTL settings',
  retry: 'Retry',
  save: 'Save and apply',
  saving: 'Applying…',
  saved: 'TTL policy applied.',
  saveFailed:
    'TTL was not fully applied. The configuration may already be saved. Retry the same settings to finish applying it.',
} as const
