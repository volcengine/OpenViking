import { OV_PATHS } from '../../client/paths.js';
import type { CommandSpec } from './command-specs.js';
import type { Argument } from './command-parser.js';
import { ConsoleCommandError } from './command-messages.js';

const wikiSkill = 'viking://system/compile/example-skills/llm-wiki/SKILL.md';
const compileName: Argument = {
  name: 'compile-name',
  valueKey: 'compile_name',
  placeholder: 'name',
  pattern:
    /^(?:[A-Za-z0-9_][A-Za-z0-9_-]{0,63}|[A-Za-z0-9_〇\p{Unified_Ideograph}][A-Za-z0-9_〇\p{Unified_Ideograph}-]{0,31})$/u,
  required: true,
};
const compileInputs: readonly Argument[] = [
  {
    name: 'from',
    kind: 'uri-list',
    placeholder: 'source-uri',
    descriptionKey: 'compile-from',
    required: true,
  },
  {
    name: 'to',
    kind: 'uri',
    placeholder: 'target-uri',
    descriptionKey: 'compile-to',
    required: true,
  },
  { name: 'skill', kind: 'uri', placeholder: 'skill-uri', required: true },
  { name: 'instruction', placeholder: 'text' },
];

export const COMPILE_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'compile',
    preferredFlags: [compileName],
    flags: [
      { ...compileName, required: false },
      ...compileInputs.map((flag) => ({ ...flag, required: false })),
    ],
    skeleton: 'write',
    request: ({ compile_name: name, from, to, skill, instruction }) => {
      if (name !== undefined) {
        if ([from, to, skill, instruction].some((value) => value !== undefined))
          throw new ConsoleCommandError('compileNamedRunInputs');
        return {
          method: 'POST',
          path: OV_PATHS.compile,
          body: { compile_name: name },
        };
      }
      if (!from)
        throw new ConsoleCommandError(
          'missingArgument',
          to ? '--from' : '--compile-name',
        );
      if (!to) throw new ConsoleCommandError('missingArgument', '--to');
      return {
        method: 'POST',
        path: OV_PATHS.compile,
        body: { from, to, skill: skill ?? wikiSkill, instruction },
      };
    },
  },
  {
    name: 'compile create',
    flags: [
      compileName,
      ...compileInputs,
      {
        name: 'args',
        kind: 'json-object',
        placeholder: 'json-object',
        descriptionKey: 'compile-args',
      },
    ],
    skeleton: 'write',
    request: (body) => ({
      method: 'POST',
      path: OV_PATHS.compileCreate,
      body,
    }),
  },
];
