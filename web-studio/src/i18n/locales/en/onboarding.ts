export default {
  accountAuth: {
    loginTitle: 'Sign in to OpenViking',
    loginDescription: 'Sign in with your work email to open your workspace.',
    emailLogin: 'Sign in with email',
    apiKeyLogin: 'Sign in with API Key',
    email: 'Work email',
    password: 'Password',
    signIn: 'Sign in',
    forgotPassword: 'Forgot password?',
    forgotTitle: 'Forgot password',
    forgotDescription:
      'Enter your work email to request a password reset link.',
    sendReset: 'Send reset link',
    backToLogin: 'Back to email sign-in',
    resetTitle: 'Reset password',
    resetDescription: 'Choose a new password for your OpenViking account.',
    newPassword: 'New password',
    confirmPassword: 'Confirm password',
    resetSubmit: 'Reset password',
    requestReset: 'Request a new reset link',
    missingResetLink:
      'This reset link is incomplete. Request a new link to continue.',
    invalidEmail: 'Enter a valid email address.',
    requiredPassword: 'Enter your password.',
    passwordRule:
      'Use at least 8 characters, including a letter, a number and a symbol.',
    mismatch: 'The passwords do not match.',
    showPassword: 'Show {{field}}',
    hidePassword: 'Hide {{field}}',
    unavailable:
      'Email account services are not available on this site yet. Use an API Key to sign in, or contact your administrator.',
    inviteTitle: 'Join a team workspace',
    inviteDescription:
      'Your invitation must be verified before you can view the team and data space or accept it.',
    missingInviteLink:
      'This invitation link is incomplete. Ask the inviter to send a new link.',
    inviteHelp:
      'You can register or sign in and choose whether to join after the invitation is verified. Opening this link does not add you to a team.',
  },
  apiLogin: {
    tagline: 'Keep every team member’s Agent in sync.',
    title: 'Connect to OpenViking',
    cloudTitle: 'Sign in with API Key',
    description:
      'Connect to a self-hosted or Volcengine service with your API Key.',
    cloudDescription: 'Use your Volcengine API Key to open your workspace.',
    serviceType: 'Service type',
    opensource: 'Self-hosted',
    volcengine: 'Volcengine hosted',
    submit: 'Sign in',
    showKey: 'Show API Key',
    hideKey: 'Hide API Key',
    language: '中文',
    keyHelp:
      'User and Admin keys open your workspace. Root keys open administration. Development servers may allow an empty key.',
    deploymentError:
      'Invalid Studio deployment configuration. Contact the site administrator.',
    connectionNotAllowed:
      'This Studio only connects to its configured Volcengine service.',
  },

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
