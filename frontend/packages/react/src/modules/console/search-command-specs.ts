import { OV_PATHS } from '../../client/paths.js';
import { ConsoleCommandError } from './command-messages.js';
import type { Argument, ArgumentValues } from './command-parser.js';
import { PATTERN_SEARCH_COMMAND_SPECS } from './pattern-search-command-specs.js';
import type { CommandSpec } from './command-specs.js';

const image: Argument = {
  name: 'image',
  placeholder: 'url',
  valueKey: 'image_url',
  aliases: ['--image-url', '--image_url'],
};

const target: Argument = {
  name: 'uri',
  alias: '-u',
  aliases: ['--target-uri', '--target_uri'],
  kind: 'uri-or-list',
  valueKey: 'target_uri',
  descriptionKey: 'search-target-uri',
};

const nodeLimit: Argument = {
  name: 'node-limit',
  alias: '-n',
  aliases: ['--node_limit'],
  kind: 'integer',
  min: 1,
  placeholder: 'n',
  valueKey: 'node_limit',
};

const threshold: Argument = {
  name: 'threshold',
  alias: '-t',
  aliases: ['--score-threshold', '--score_threshold'],
  kind: 'number',
  placeholder: 'score',
  valueKey: 'score_threshold',
};

const level: Argument = {
  name: 'level',
  alias: '-L',
  kind: 'integer-list',
  placeholder: '0,1,2',
  max: 2,
};

const before: Argument = {
  name: 'before',
  aliases: ['--until'],
  placeholder: 'time',
  valueKey: 'until',
};

const after: Argument = {
  name: 'after',
  aliases: ['--since'],
  placeholder: 'time',
  valueKey: 'since',
};

const timeDecay: Argument = {
  name: 'events-time-decay-protection',
  aliases: ['--events_time_decay_protection'],
  placeholder: 'duration',
  pattern: /^(?:0|\d+[mhd])$/,
  valueKey: 'events_time_decay_protection',
};

const retrievalOptions: readonly Argument[] = [
  { name: 'filter', kind: 'json-object' },
  {
    name: 'include-provenance',
    aliases: ['--include_provenance'],
    kind: 'boolean',
    valueKey: 'include_provenance',
  },
  { name: 'telemetry', kind: 'boolean' },
];

const mode: Argument = {
  name: 'mode',
  choices: ['list', 'context'],
  placeholder: 'list|context',
};

const sessionId: Argument = {
  name: 'session-id',
  aliases: ['--session_id'],
  valueKey: 'session_id',
  placeholder: 'id',
  descriptionKey: 'search-session-id',
};

const retrievalLimit: Argument = {
  ...nodeLimit,
  valueKey: 'limit',
  aliases: ['--node_limit', '--limit'],
  helpDefault: 10,
};
const contextType: Argument = {
  name: 'context-type',
  valueKey: 'context_type',
  placeholder: 'type',
};
const searchTags: Argument = { name: 'tags', placeholder: 'k=v' };
const readContent: Argument = {
  name: 'read-content',
  valueKey: 'read_content',
  kind: 'boolean',
};
const query: Argument = { name: 'query', optional: true, rest: true };

function retrievalRequest(name: 'find' | 'search', values: ArgumentValues) {
  const { context_type, tags, ...body } = values;
  const query = typeof body.query === 'string' ? body.query.trim() : '';
  const acceptsFilterOnly = name === 'find' || body.mode === 'context';
  const filter =
    body.filter || body.since || body.until || context_type || tags;
  if (!query && !body.image_url && !(acceptsFilterOnly && filter))
    throw new ConsoleCommandError('missingArgument', 'query / --image');

  if (body.mode === 'context') {
    for (const [key, flag] of [
      ['target_uri', '--uri'],
      ['level', '--level'],
      ['include_provenance', '--include-provenance'],
    ] as const) {
      if (body[key] !== undefined)
        throw new ConsoleCommandError(
          'invalidArgument',
          `${flag} / --mode context`,
        );
    }
  }

  return {
    method: 'POST' as const,
    path: name === 'find' ? OV_PATHS.searchFind : OV_PATHS.searchQuery,
    body: {
      ...body,
      ...(typeof context_type === 'string'
        ? { context_type: context_type.split(',') }
        : {}),
      ...(typeof tags === 'string' ? { tags: tags.split(',') } : {}),
    },
  };
}

export const SEARCH_COMMAND_SPECS: readonly CommandSpec[] = [
  ...(['find', 'search'] as const).map((name): CommandSpec => {
    const common: Argument[] =
      name === 'search'
        ? [
            image,
            mode,
            sessionId,
            {
              name: 'search-type',
              valueKey: 'search_type',
              choices: ['semantic', 'keywords'],
              helpDefault: 'semantic',
            },
          ]
        : [image];
    const flags = [
      ...common,
      target,
      retrievalLimit,
      threshold,
      ...(name === 'find' ? [level, after, before] : [after, before, level]),
      contextType,
      searchTags,
      readContent,
      timeDecay,
      {
        name: 'time-field',
        valueKey: 'time_field',
        choices: ['updated_at', 'created_at'],
      },
    ];
    return {
      name,
      positional: [query],
      flags: [...flags, ...retrievalOptions],
      preferredFlags: flags.map((flag) => {
        if (flag === mode) return { ...mode, defaultValue: 'list' };
        return flag;
      }),
      skeleton: 'search',
      request: (body) => retrievalRequest(name, body),
    };
  }),
  ...PATTERN_SEARCH_COMMAND_SPECS,
];
