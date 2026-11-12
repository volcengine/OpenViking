import type { TuiCommandEntry } from './shared';

/** Demo catalogue for saved compilation configurations; no execution adapter. */
export const COMPILE_COMMANDS: readonly TuiCommandEntry[] = [
  {
    name: 'compile',
    group: 'core',
    description: 'Compile imported resources with a Skill',
    usage: '/compile --compile-name <name>',
    skeleton: 'write',
    options: [
      {
        token: '--compile-name <name>',
        required: true,
        description: 'Name of an existing compilation configuration',
      },
    ],
    examples: ['/compile --compile-name demo-wiki'],
  },
  {
    name: 'compile create',
    group: 'core',
    description: 'Save a compilation configuration without running it',
    usage:
      '/compile create --compile-name <name> --from <source-uri> --to <target-uri> --skill <skill-uri>',
    skeleton: 'write',
    options: [
      {
        token: '--compile-name <name>',
        required: true,
        description: 'Unique name for the new compilation configuration',
      },
      {
        token: '--from <source-uri>',
        required: true,
        description:
          'Existing source directories; repeat or separate with commas',
      },
      {
        token: '--to <target-uri>',
        required: true,
        description: 'Output directory',
      },
      {
        token: '--skill <skill-uri>',
        required: true,
        description: 'Skill directory URI',
      },
      {
        token: '--instruction <text>',
        description: 'Additional compilation instructions',
      },
      {
        token: '--args <json-object>',
        description: 'Extra model and scheduling options as a JSON object',
      },
    ],
    examples: [
      '/compile create --compile-name demo-wiki --from viking://resources/acme --to viking://resources/acme-wiki --skill viking://agent/skills/llm-wiki',
    ],
  },
];
