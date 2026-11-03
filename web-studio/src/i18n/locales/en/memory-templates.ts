export default {
  tab: 'Extraction rules',
  title: 'Memory extraction rules',
  subtitle:
    'Configure extraction instructions for memory types and fields in this account. New rules apply to future extraction.',
  account: 'Current account: {{account}}',
  fileName: '{{type}}.md',
  userGroup: 'User context',
  userGroupHint:
    'Remember who the user is, what matters to them, and what happened',
  agentGroup: 'Agent identity and continuity',
  agentGroupHint:
    'Keep the assistant’s identity, boundaries, and expression consistent',
  profile: 'Profile',
  preferences: 'Preferences',
  entities: 'Entities',
  events: 'Events',
  soul: 'Soul',
  identity: 'Identity',
  profileHint: 'Stable identity, background, and long-term context.',
  preferencesHint: 'Preferences, dislikes, and habits organized by topic.',
  entitiesHint:
    'Important people, organizations, places, products, and relationships.',
  eventsHint:
    'Specific events, decisions, commitments, and outcomes worth recalling.',
  soulHint:
    'The assistant’s enduring principles, boundaries, style, and continuity.',
  identityHint: 'The assistant’s name, identity, style, and introduction.',
  custom: 'Customized',
  default: 'System default',
  back: 'Back to extraction rules',
  loading: 'Loading memory templates…',
  retry: 'Retry',
  accessDenied:
    'Account administrator access is required to configure extraction rules.',
  openConnection: 'Check connection settings',
  typeDescription: 'Memory type description',
  typeDescriptionHint:
    'Determines which information triggers extraction or an update.',
  fieldDescription: 'Field extraction instruction',
  contentTemplate: 'Memory file content template',
  contentTemplateHint: 'Organizes extracted fields into the memory file body.',
  edit: 'Configure rules',
  cancel: 'Cancel',
  save: 'Publish now',
  saving: 'Saving…',
  reset: 'Restore system default',
  resetField: 'Restore default',
  confirmReset:
    'All custom instructions for this type will return to system defaults. Existing memories will not be rewritten.',
  confirm: 'Restore defaults',
  saved: 'Extraction rules published',
  resetDone: 'System defaults restored',
  saveFailed: 'Save failed',
  resetFailed: 'Reset failed',
  required: 'Extraction instructions cannot be empty.',
  tooLong: 'Each extraction instruction is limited to 50,000 characters.',
  example: 'Example memory file',
  exampleHint:
    'Path variables are filled when memory is generated; the example body is not generated from your edits.',
  examples: {
    profile:
      '# User profile\n- Occupation: product designer\n- Communication: conclusions and evidence first\n- Long-term interest: knowledge management',
    preferences:
      '# Working style\n- Prefers concise conclusions first\n- Likes reproducible examples',
    entities:
      '# Example project\n- Type: project\n- Relationship: a product the user works on',
    events:
      '# An important decision\n- Date: 2026-08-19\n- Adopted a new workflow\n- Review its impact later',
    soul: "# Assistant principles\n- Represent known facts accurately\n- Explain uncertainty\n- Respect the user's boundaries",
    identity:
      '# Assistant identity\n- Name: Example assistant\n- Style: clear and direct\n- Introduction: helps organize information and complete tasks',
  },
  variable: 'Available variable',
  languageVariable: 'language +',
  variableHint: 'Click to append to the current instruction',
  syntax: 'Jinja syntax',
  syntaxHint:
    'Descriptions support restricted Jinja expressions, validated by the server on save.',
  syntaxExample: "{{ language or 'English' }} · {% if language %}…{% endif %}",
  changed: 'Unsaved changes',
  description: 'description',
  fieldPath: 'fields.{{field}}.description',
  contentPath: 'content_template',
  fields: {
    content: 'Profile body',
    topic: 'Preference topic',
    category: 'Entity category',
    name: 'Name',
    event_name: 'Event name',
    summary: 'Event summary',
    core_truths: 'Core principles',
    boundaries: 'Boundaries',
    vibe: 'Style',
    continuity: 'Continuity',
    creature: 'Identity concept',
    avatar: 'Avatar',
    emoji: 'Emoji',
    introduction: 'Introduction',
  },
} as const
