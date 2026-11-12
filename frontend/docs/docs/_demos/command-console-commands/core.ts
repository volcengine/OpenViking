import { LIMIT_OPTION, URI_PARAM } from './shared';
import type { CommandConsoleCommandEntry } from './shared';

/** 核心流程与检索命令。Core workflow and retrieval commands. */
export const CORE_COMMANDS: readonly CommandConsoleCommandEntry[] = [
  {
    name: 'add-resource',
    skeleton: 'write',
    group: 'core',
    summary: 'Add resources into OpenViking',
    description:
      'Import a web URL, repository or accessible path into Resources for searching, reading and citing. Local file upload is not supported here.',
    usage: '/add-resource <path-or-url> [--to <uri>] [--parent <uri>]',
    params: [
      { label: '<path-or-url>', description: 'Local path or URL to import' },
      { label: '--to <uri>', description: 'Exact target URI' },
      { label: '--parent <uri>', description: 'Existing parent directory URI' },
      { label: '--wait', description: 'Wait until processing completes' },
    ],
    examples: [
      '/add-resource https://example.com/spec.md --to viking://specs/api.md',
      '/add-resource ./docs --parent viking://projects/acme --wait',
    ],
  },
  {
    name: 'add-skill',
    skeleton: 'write',
    group: 'core',
    description: 'Add skills from a source',
    usage: '/add-skill <source> [-p <target-uri>] [--wait]',
    params: [
      {
        label: '<source>',
        description: 'Skill directory, SKILL.md or content',
      },
      { label: '--wait', description: 'Wait until processing completes' },
    ],
    options: [
      { token: '-p <target-uri>', description: 'Skill target root directory' },
    ],
    examples: ['/add-skill ./skills/my-skill --wait'],
  },
  {
    name: 'add-memory',
    skeleton: 'write',
    group: 'core',
    description: 'Add memory in one shot',
    usage: '/add-memory <content> [--session-id <id>]',
    params: [{ label: '<content>', description: 'Content to memorize' }],
    options: [
      {
        token: '--session-id <id>',
        description: 'Session used for this memory example',
      },
    ],
    examples: [
      '/add-memory "Release checklist lives in viking://resources/release"',
    ],
  },
  {
    name: 'find',
    group: 'search',
    description: 'Run semantic retrieval',
    usage: '/find <query> [-u <uri>] [-n <n>] [--level <0,1,2>]',
    skeleton: 'search',
    params: [{ label: '<query>', description: 'Search query' }],
    options: [
      { token: '-u, --uri <uri>', description: 'Target URI' },
      LIMIT_OPTION,
      { token: '-t, --threshold <score>', description: 'Score threshold' },
      {
        token: '-L, --level <0,1,2>',
        description: 'Only include these levels',
      },
    ],
    examples: ['/find "release checklist" -u viking://resources/acme -n 5'],
  },
  {
    name: 'search',
    group: 'search',
    description: 'Run context-aware retrieval',
    usage: '/search <query> [--mode <list|context>] [--search-type <type>]',
    skeleton: 'search',
    params: [{ label: '<query>', description: 'Search query' }],
    options: [
      {
        token: '--mode <list|context>',
        description: 'Ranked hits or assembled context',
      },
      { token: '--search-type <type>', description: 'semantic or keywords' },
      { token: '--session-id <id>', description: 'Session for context search' },
      LIMIT_OPTION,
    ],
    examples: ['/search "onboarding flow" --mode context --session-id demo'],
  },
  {
    name: 'grep',
    group: 'search',
    description: 'Run content pattern search',
    usage: '/grep <pattern> [-u <uri>] [-i]',
    skeleton: 'search',
    params: [{ label: '<pattern>', description: 'Search pattern' }],
    options: [
      { token: '-u, --uri <uri>', description: 'Target URI' },
      { token: '-i, --ignore-case', description: 'Case insensitive' },
      { token: '-a, --after-context <n>', description: 'Lines after a match' },
      LIMIT_OPTION,
    ],
    examples: ['/grep "TODO" -u viking://resources/acme -i'],
  },
  {
    name: 'glob',
    group: 'search',
    description: 'Run file glob pattern search',
    usage: '/glob <pattern> [-u <uri>]',
    skeleton: 'search',
    params: [{ label: '<pattern>', description: 'Glob pattern' }],
    options: [
      { token: '-u, --uri <uri>', description: 'Search root URI' },
      LIMIT_OPTION,
    ],
    examples: ['/glob "**/*.md" -u viking://resources/acme'],
  },
  {
    name: 'read',
    group: 'search',
    description: 'Read file content (Level 2)',
    usage: '/read <uri> [--offset <n>] [--limit <n>]',
    skeleton: 'resource',
    params: [URI_PARAM],
    options: [
      { token: '--offset <n>', description: 'Starting line offset' },
      { token: '--limit <n>', description: 'Number of lines, -1 for all' },
    ],
    examples: ['/read viking://resources/acme/index.md'],
  },
  {
    name: 'abstract',
    group: 'search',
    description: 'Read abstract content (Level 0)',
    usage: '/abstract <directory-uri>',
    skeleton: 'resource',
    params: [{ label: '<directory-uri>', description: 'Directory URI' }],
    examples: ['/abstract viking://resources/acme'],
  },
  {
    name: 'overview',
    group: 'search',
    description: 'Read overview content (Level 1)',
    usage: '/overview <directory-uri>',
    skeleton: 'resource',
    params: [{ label: '<directory-uri>', description: 'Directory URI' }],
    examples: ['/overview viking://resources/acme'],
  },
];
