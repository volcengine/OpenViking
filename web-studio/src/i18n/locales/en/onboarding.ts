export default {
  directoryOptions: 'Choose another data identity (advanced)',
  rootSetupTitle: 'Start using the workbench',
  rootSetupDescription:
    'A Root key manages the service and cannot access user data directly. Current Account: {{account}}. Create a user, then enter the workbench from the credential result. Creating a teammate never replaces your workbench identity automatically.',
  rootSetupCreate: 'Create a workbench identity',
  rootSetupExisting: 'Configure an existing User key',
  verifyDirectoryHint:
    'Optional. Add a separate management credential. Account Admin manages its Account; Root manages the service and all Accounts.',
  loadDirectory: 'Load Accounts & Users',
  directoryHint: 'Use a Root or Admin API Key to browse identities.',
  directoryControlRequired:
    'Enter a valid Root or Admin API Key to load accounts and users.',
  directoryLoadFailed:
    'Could not load identities. Check the management key and service address, then retry.',
  directoryAccount: 'Select Account',
  directoryUser: 'Select User',
  directoryAdminHint:
    'An Admin key can select users only in its own account. Enter that user’s API Key to access data.',
  directoryKeyHint:
    'Selecting an identity does not grant data access. Enter its User API Key, or leave it empty to manage accounts only.',
  directoryIdentityMismatch:
    'This User API Key belongs to a different account or user. Enter the key for the selected identity.',

  openSourceConnection: 'OpenSource',
  cancel: 'Cancel',
  exit: 'Exit Studio',
  connectionTitle: 'Connect to OpenViking.',
  preferencesHint: 'Choose your language and appearance.',
  connectionHint: 'Connect and verify an OpenViking service.',

  verifiedManagement:
    'Management access verified. Open Users to create a User API Key.',
  brand: 'OpenViking Studio',
  invalidService:
    'This address did not return valid OpenViking service information.',
  keyRequired: 'This service requires an API Key. Enter it and retry.',

  steps: 'Choose a workspace',
  workspace: 'Choose a service',
  workspaceHint:
    'Choose the service type and address for your first connection.',
  savedWorkspaces: 'Saved connections',
  connectionChoice: 'Choose a connection',
  newConnection: 'Add a new service address',
  savedConnectionUnavailable:
    'This saved connection is unavailable. Choose another one or enter a new API Key.',
  newAddressHint: 'Enter a valid HTTP or HTTPS address to continue.',
  hostedEndpoint: 'Service Address',
  preferences: 'Preferences',
  connection: 'Connect a service',
  welcome: 'Welcome to\nOpenViking Studio',
  welcomeDescription:
    'Choose a saved connection or add a new one, then set your language and appearance.',
  connectionDescription:
    'Configure and verify your connection to open the appropriate workspace and features.',
  language: 'Language',
  theme: 'Appearance',
  light: 'Light',
  dark: 'Dark',
  system: 'System',
  next: 'Next',
  back: 'Back',
  verify: 'Verify connection',
  verifying: 'Verifying…',
  verified: 'Connection verified',
  verifyFailed: 'Connection verification failed:',
  healthRequestFailed: 'Service health check failed:',
  dataRequestFailed: 'Memory access check failed:',
  enter: 'Enter Studio',
  keyHelp:
    'Enter a User, Account Admin or Root key. Verification identifies its permissions and identity automatically. Development mode needs no key.',
  keyLabel: 'API Key',
  hostedKeyHelp: 'Enter your Volcengine User API Key to access your memories.',
  advanced: 'Advanced open-source connection (optional)',
  loadUsers: 'Load users in this account',
  loadingUsers: 'Loading users…',
  chooseUser: 'Choose a user',
  noUsers:
    'No users were found in this account. Check the account ID or enter a user ID manually.',
  usersDevMode:
    'This service is in development mode and does not provide a user list. Enter the account and user IDs manually to access that space.',
  usersNeedKey:
    'This service requires a Root or admin API key to list users. Enter the key and try again.',
  usersUnavailable:
    'Could not load users. Check the management key and account, or enter a user ID manually.',
  userSelectionHint:
    'Use a management key to load users. In dev mode or without management access, enter the account and user IDs manually. API Key mode also requires that user’s key.',
  storageError:
    'Unable to save your connection. Allow local storage for this site and retry.',
}
