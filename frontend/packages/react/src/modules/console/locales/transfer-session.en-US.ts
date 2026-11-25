export const transferSession = {
  get: 'Download a file',
  import: 'Import an OVPack',
  export: 'Export an OVPack',
  backup: 'Back up public scopes',
  restore: 'Restore a backup',
  'session new': 'Create a session',
  'session list': 'List sessions',
  'session get': 'Get session details',
  'session get-session-context': 'Get assembled session context',
  'session get-session-archive': 'Get one session archive',
  'session delete': 'Delete a session',
  'session add-message': 'Add a session message',
  'session add-messages': 'Add a batch of session messages',
  'session commit': 'Commit a session',
  'session config set': 'Update session configuration',
};

export const transferErrors = {
  unavailableCommand: (value?: string) => `Command unavailable: ${value}`,
  invalidOvpack: () => 'Select a valid .ovpack file.',
  invalidOvpackDownload: () => 'The server did not return an OVPack archive.',
  missingUploadId: () => 'The upload did not return a temporary file ID.',
};
