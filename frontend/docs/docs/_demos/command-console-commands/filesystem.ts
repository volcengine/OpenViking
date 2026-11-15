import { LIMIT_OPTION, URI_PARAM } from './shared';
import type { CommandConsoleCommandEntry } from './shared';

/** 文件系统命令。Filesystem commands. */
export const FILESYSTEM_COMMANDS: readonly CommandConsoleCommandEntry[] = [
  {
    name: 'ls',
    aliases: ['list'],
    group: 'filesystem',
    description: 'List directory contents',
    usage: '/ls [uri] [--recursive] [--simple]',
    skeleton: 'tree',
    params: [
      { label: '[uri]', description: 'Viking URI, defaults to viking://' },
    ],
    options: [
      {
        token: '-r, --recursive',
        description: 'List subdirectories recursively',
      },
      { token: '-s, --simple', description: 'Simple path output' },
      LIMIT_OPTION,
    ],
    examples: ['/ls viking://projects/acme --recursive'],
  },
  {
    name: 'tree',
    group: 'filesystem',
    description: 'Get directory tree',
    usage: '/tree <uri> [-L <n>] [--directories-only]',
    skeleton: 'tree',
    params: [URI_PARAM],
    options: [
      {
        token: '-L, --level-limit <n>',
        description: 'Maximum depth, default 3',
      },
      { token: '--directories-only', description: 'Only include directories' },
    ],
    examples: ['/tree viking://projects/acme -L 4'],
  },
  {
    name: 'mkdir',
    group: 'filesystem',
    description: 'Create directory',
    usage: '/mkdir <uri> [--description <text>]',
    skeleton: 'write',
    params: [URI_PARAM],
    options: [
      { token: '--description <text>', description: 'Initial description' },
    ],
    examples: ['/mkdir viking://projects/acme --description "ACME project"'],
  },
  {
    name: 'rm',
    aliases: ['del', 'delete'],
    group: 'filesystem',
    description: 'Remove resource',
    usage: '/rm <uri> [--recursive] [--wait]',
    skeleton: 'write',
    params: [URI_PARAM],
    options: [
      { token: '-r, --recursive', description: 'Remove recursively' },
      { token: '--wait', description: 'Wait for semantic refresh' },
    ],
    examples: ['/rm viking://scratch/old-note.md'],
  },
  {
    name: 'cp',
    group: 'filesystem',
    description: 'Copy a file or directory',
    usage: '/cp <source> <target> [--recursive]',
    skeleton: 'write',
    params: [
      { label: '<source>', description: 'Source URI' },
      { label: '<target>', description: 'Target URI' },
    ],
    options: [{ token: '-r, --recursive', description: 'Copy recursively' }],
    examples: ['/cp -r viking://resources/src viking://resources/backup'],
  },
  {
    name: 'mv',
    aliases: ['rename'],
    group: 'filesystem',
    description: 'Move or rename resource',
    usage: '/mv <from-uri> <to-uri>',
    skeleton: 'write',
    params: [
      { label: '<from-uri>', description: 'Source URI' },
      { label: '<to-uri>', description: 'Target URI' },
    ],
    examples: ['/mv viking://notes/draft.md viking://notes/final.md'],
  },
  {
    name: 'stat',
    group: 'filesystem',
    description: 'Get resource metadata',
    usage: '/stat <uri>',
    skeleton: 'status',
    params: [URI_PARAM],
    examples: ['/stat viking://projects/acme/spec.md'],
  },
  {
    name: 'get',
    group: 'filesystem',
    description: 'Download file to local path',
    usage: '/get <uri> <local-path>',
    skeleton: 'write',
    params: [
      URI_PARAM,
      { label: '<local-path>', description: 'Local path that must not exist' },
    ],
    examples: ['/get viking://resources/acme/spec.pdf ./spec.pdf'],
  },
  {
    name: 'write',
    group: 'filesystem',
    description: 'Write text content to a file',
    usage: '/write <uri> [--content <text>] [--mode <mode>]',
    skeleton: 'write',
    params: [URI_PARAM],
    options: [
      { token: '--content <text>', description: 'Content to write' },
      { token: '--from-file <path>', description: 'Read from a local file' },
      { token: '--mode <mode>', description: 'replace, append or create' },
    ],
    examples: ['/write viking://resources/acme/notes.md --content "Hello"'],
  },
];
