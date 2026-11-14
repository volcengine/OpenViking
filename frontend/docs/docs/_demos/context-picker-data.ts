export const contextPickerCatalog = [
  { uri: 'viking://user', name: 'user', parent: '' },
  { uri: 'viking://user/default', name: 'default', parent: 'viking://user' },
  {
    uri: 'viking://user/default/resources',
    name: 'resources',
    parent: 'viking://user/default',
  },
  { uri: 'viking://resources', name: 'resources', parent: '' },
  {
    uri: 'viking://resources/compiled',
    name: 'compiled',
    parent: 'viking://resources',
  },
  {
    uri: 'viking://resources/wiki',
    name: 'wiki',
    parent: 'viking://resources',
  },
  {
    uri: 'viking://resources/wiki/archive',
    name: 'archive',
    parent: 'viking://resources/wiki',
  },
  {
    uri: 'viking://resources/read-only',
    name: 'read-only',
    parent: 'viking://resources',
  },
  { uri: 'viking://agent', name: 'agent', parent: '' },
  { uri: 'viking://system', name: 'system', parent: '' },
  {
    uri: 'viking://system/compile',
    name: 'compile',
    parent: 'viking://system',
  },
  {
    uri: 'viking://system/compile/example-skills',
    name: 'example-skills',
    parent: 'viking://system/compile',
  },
  {
    uri: 'viking://system/compile/example-skills/llm-wiki',
    name: 'llm-wiki',
    parent: 'viking://system/compile/example-skills',
  },
  {
    uri: 'viking://system/compile/example-skills/llm-wiki/SKILL.md',
    name: 'SKILL.md',
    parent: 'viking://system/compile/example-skills/llm-wiki',
  },
];
