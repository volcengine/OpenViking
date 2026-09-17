export default {
  replyMode: 'Group reply mode',
  withoutMention: 'Reply without @mention',
  mentionModeHint:
    'Reply only when mentioned in group chats. Direct messages are unaffected.',
  withoutMentionHint:
    'No mention required in regular groups or the first message of a topic. Later topic replies still require a mention, except in DEBUG mode. Direct messages are unaffected.',
  groupMessagePermission:
    'Requires Feishu permission to receive all group messages. QR setup requests it automatically; for manual connections or later changes, enable the permission and publish the app in the Feishu developer console.',
  saveReplyMode: 'Save reply mode',
  savingReplyMode: 'Saving…',

  operationFailed:
    'The operation could not be completed. Please retry. Details:',
  viewSetup: 'View configuration',
  chooseBotChannel: 'Choose a platform for your bot.',
  searchBots: 'Search bots or runtime users',
  botCount: '{{count}} bots',
  noMatchingBots: 'No bots match your filters.',
  channelFilter: 'Channel',
  addBot: 'Add bot',
  botsHint: 'Manage your bots and filter by channel.',
  platformReady: 'Scan to connect, then add your bot to Feishu groups.',
  connectedBots: 'Connected bots',
  noConnectedBots: 'No bots yet. Select “Add bot” to get started.',
  dingtalk: 'DingTalk',

  qr: {
    manualRecovery: 'Continue with manual setup',
    intro:
      'Choose a runtime user, scan with Feishu to configure your bot, then add it to a group.',
    choose: 'Choose user',
    scan: 'Scan with Feishu',
    addGroup: 'Add to group',
    close: 'Close',
    botName: 'Bot name',
    start: 'Generate Feishu QR code',
    manual: 'Already have an app? Connect manually',
    consent:
      'Confirming the scan creates an app in your Feishu organization, configures group mentions, message sending and chat information permissions, and submits a release.',
    scanHint:
      'Scan with the Feishu app and confirm on your phone. The code expires in about 2 minutes. App creation and configuration follow automatically.',
    approvalHint:
      'The release is awaiting your organization’s approval. You can close this page and return later.',
    retryScan: 'Scan again and continue',
    cancel: 'Cancel scan',
    copyFailed: 'Copy failed. Select and copy the text above manually.',
    visibilityHint:
      'The initial app audience is the person who scanned. To make it available to other group members, update app availability in Feishu and publish.',
    resumeFirst:
      'This connection is paused. Resume it from the channel list before testing.',
    states: {
      initializing: 'Preparing QR code…',
      waiting_for_scan: 'Waiting for Feishu scan',
      scanned: 'Scanned — confirm on your phone',
      creating: 'Creating app…',
      configuring: 'Configuring permissions and messaging…',
      publishing: 'Submitting release…',
      awaiting_approval: 'Awaiting administrator approval',
      ready: 'App published — add it to a group',
      failed: 'Automatic setup is incomplete',
      expired: 'QR code expired',
      interrupted: 'Setup interrupted — resume when ready',
      cancelled: 'Scan cancelled',
    },
    errors: {
      expired: 'The QR code expired. Scan a new code.',
      interrupted:
        'The service restarted. Scan again to resume from the saved progress.',
      identity_changed:
        'The account or organization differs from the app creator. Scan with the original account.',
      creation_uncertain:
        'The creation result is unknown. Check Feishu for an app with this name before creating another.',
      publication_uncertain:
        'The release result is unknown. Check the version in Feishu. No version will be created or submitted again automatically.',
      approval_pending:
        'Approval is still pending. Once approved, scan again to check the release without creating another app.',
      permissions_unavailable:
        'Required permissions could not be matched in the Feishu catalog. Scan again to retry, or use manual setup if it persists.',
      events_not_ready:
        'Message events are not ready. Scan again to continue configuration.',
      connection_unavailable:
        'The bot connection is not ready. Check the server network and scan again.',
      platform_request_failed: 'The Feishu request failed. Try again later.',
      platform_rejected:
        'Feishu rejected this operation. Check your app management permissions and organization policies.',
      session_expired: 'The Feishu session expired. Scan again.',
      identity_unavailable:
        'The account and organization could not be verified. No app was created.',
      untrusted_redirect:
        'This Feishu login redirect is not supported. Setup stopped.',
      login_unavailable: 'A QR code could not be generated. Try again later.',
      icon_upload_failed: 'Bot icon upload failed. Please retry.',
      credentials_unavailable:
        'The app exists, but its credentials could not be read. Scan again to resume.',
      setup_failed:
        'Setup is incomplete. Scan again to resume from saved progress. If it fails again, check the app in Feishu.',
    },
  },
  runtimeUser: 'Run as user',
  selectUser: 'Select a user',
  runtimeUserHint:
    'Select an ordinary user in this account. The bot uses their resource and memory permissions. Credentials are bound by the server; no API key copying is needed.',
  userUnavailable: 'Credential unavailable',
  noUsers: 'No ordinary users yet. Create a user, then refresh this list.',
  manageUsers: 'Manage users',
  rotateHint:
    'Leave the application secret empty to keep it unchanged. The server refreshes the bound user credential automatically. Failed validation keeps the existing connection.',
  rotate: 'Update credentials',
  cancelEdit: 'Cancel',

  saveCredentials: 'Validate and save',
  credentialsSaved: 'Credentials updated',
  title: 'VikingBot',
  conversations: 'Conversations',
  channels: 'Bots',
  intro:
    'Talk to VikingBot here, or connect it to your team’s messaging platform.',
  newChat: 'New conversation',
  web: 'Web',
  feishu: 'Feishu',
  all: 'All',
  search: 'Search conversations',
  empty: 'Start a conversation',
  emptyHint:
    'Ask a question, explore your resources, or connect a bot to your messaging platform.',
  manageBots: 'Manage bots',
  enable: 'Enable VikingBot to begin',
  enableHint:
    'Ask your administrator to start OpenViking with Bot support and configure a conversation model.',
  modelHint: 'VikingBot inherits the root vlm model configuration by default.',
  retry: 'Retry',
  loading: 'Loading…',
  error: 'Something went wrong: {{error}}',
  addFeishu: 'Connect Feishu',
  comingSoon: 'Coming soon',
  adminOnly:
    'Only the server administrator can manage connections and view Feishu history.',
  webReady: 'Web conversations use your current OpenViking identity.',
  manageHint:
    'Connect an application once, then add its bot to multiple groups.',
  steps: [
    'Create application',
    'Connect application',
    'Permissions & events',
    'Publish application',
    'Add to group & verify',
    'Start chatting',
  ],
  setupTitle: 'Connect VikingBot to Feishu',
  setupHint:
    'You need permission to manage a Feishu enterprise application and add its bot to an internal group.',
  createHint:
    'Create an enterprise custom application in Feishu Open Platform, set its name and avatar, then add the Bot capability. A group webhook cannot receive conversations.',
  openPlatform: 'Open Feishu Open Platform',
  next: 'Continue',
  back: 'Back',
  close: 'Save and close',
  credentialsHint:
    'Find App ID and App Secret under Credentials & Basic Info. Studio validates them and starts the long connection before you configure events.',
  appId: 'App ID',
  appSecret: 'App Secret',
  userKey: 'Dedicated OpenViking user API key',
  userKeyHint:
    'Create an ordinary user specifically for this bot in the current account. Do not reuse your personal key. Root and admin keys are rejected. Grant this user access only to resources intended for group members.',
  connect: 'Validate and connect',
  connectedAs: 'Bot identity: {{name}} · OpenViking user: {{user}}',
  permissionsHint:
    'Enable receiving group @bot messages and sending messages as the bot. Add receiving direct messages if you need private chat. Enable im:chat.members:read and publish a new version to display member names.',
  eventsHint:
    'Under Events & Callbacks, choose long connection and subscribe to im.message.receive_v1. No public callback URL is needed. Wait for the connection before saving the subscription.',
  officialDocs: 'Open official event documentation',
  permissionsNote:
    'Permissions and publication cannot be fully inspected here. The group test below verifies actual message reception and delivery.',
  publishHint:
    'Create a version in Version Management & Release, set the availability range, and submit it. If approval is required, return after the administrator approves it. Changes to permissions may require a new release.',
  publishDone: 'I have published the application',
  groupHint:
    'In Feishu, open your group settings and add this application bot. Generate a test message, then use Feishu’s @ picker to mention the bot and send it. Copied plain text is not a real mention.',
  test: 'Generate connection test',
  testText: 'VikingBot connection test {{code}}',
  copy: 'Copy',
  copied: 'Copied',
  received: 'Group message received',
  sent: 'Reply accepted by Feishu',
  waiting: 'Waiting',
  verified: 'Verified',
  visible: 'I can see the test reply in the group',
  expired: 'Test expired. Generate a new test message.',
  troubleshoot:
    'No reply? Check publication and availability, bot membership, a real @mention, message permissions, event subscription, and the long connection. If a message was received, check sending permissions.',
  done: 'Feishu is connected',
  doneHint:
    'Now @ the bot with a real question. The connection test does not call the model. If normal chat fails, check the model configuration.',
  setupComplete: 'Feishu setup complete',
  startUsingHint:
    'Add this app in your Feishu group settings, then @mention it to start chatting. No test code is required.',
  noActivity: 'No activity yet',
  connectionHelp: 'Troubleshoot connection (optional)',
  deleteConnection: 'Delete connection',
  deleteConnectionHint:
    'Delete the connection for “{{title}}”? This stops the bot connection and permanently removes its Studio chat history. The Feishu app and group messages remain unchanged.',
  deleteFailed: 'Deletion failed: {{error}}',
  cancelDelete: 'Cancel',
  deletingConnection: 'Deleting…',
  confirmDeleteConnection: 'Delete',
  manualTitle: 'Connect an existing app',
  manualHint: 'Enter your Feishu app credentials and select its runtime user.',
  manualInstructions: 'Configuration: permissions, events and publication',
  manualConnected: 'App credentials verified',
  manualConnectedHint:
    'Connection saved. Configure message permissions and long-connection events, then publish the app before using it in a group.',
  finish: 'Done',
  continueSetup: 'Continue setup',
  pause: 'Pause',
  resume: 'Resume',
  state: { connected: 'Connected', connecting: 'Connecting', paused: 'Paused' },
  onlyMention: 'Responds when @mentioned',
  historyHint:
    'Only messages received since this connection was set up are shown. This is not the complete Feishu group history.',
  readonly: 'This is a Feishu conversation. Continue chatting in Feishu.',
  group: 'Feishu conversation',
  unknownSender: 'Group member',
  noHistory: 'No received messages yet',
  earlier: 'Load earlier messages',
  delivery: {
    received: 'Received',
    sent: 'Accepted by Feishu',
    send_failed: 'Sending failed',
  },
  connectionError: 'Bot service is unavailable. Check the service and retry.',
  lastReceived: 'Last received',
  lastSent: 'Last successful reply',
} as const
