import { ConsoleCommandError } from './command-messages.js';
import type { Argument, ArgumentValue } from './command-parser.js';

function parseNumber(argument: Argument, value: string, integer: boolean) {
  const number = Number(value);
  const valid = integer
    ? /^-?\d+$/.test(value) && Number.isSafeInteger(number)
    : value.trim() !== '' && Number.isFinite(number);
  if (
    !valid ||
    number < (argument.min ?? (integer ? 0 : -Infinity)) ||
    number > (argument.max ?? Infinity)
  )
    throw new ConsoleCommandError(
      'invalidArgument',
      `${argument.name}: ${value}`,
    );
  return number;
}

function readList(value: string): string[] {
  return value.split(',').map((item) => item.trim());
}

function readIntegerList(argument: Argument, value: string): number[] {
  let items: unknown = readList(value);
  if (value.trimStart().startsWith('[')) {
    try {
      items = JSON.parse(value) as unknown;
    } catch {
      throw new ConsoleCommandError(
        'invalidArgument',
        `${argument.name}: ${value}`,
      );
    }
  }
  if (
    !Array.isArray(items) ||
    items.some((item) => !['string', 'number'].includes(typeof item))
  )
    throw new ConsoleCommandError(
      'invalidArgument',
      `${argument.name}: ${value}`,
    );
  return (items as (string | number)[]).map((item) =>
    parseNumber(argument, String(item).trim(), true),
  );
}

function mergeList<T extends number | string>(
  previous: ArgumentValue | undefined,
  values: T[],
) {
  const existing = Array.isArray(previous)
    ? previous
    : previous === undefined
      ? []
      : [previous];
  return Array.from(new Set([...existing, ...values])) as T[];
}

function parseObject(
  argument: Argument,
  value: string,
): Record<string, unknown> {
  try {
    const object: unknown = JSON.parse(value);
    if (object && typeof object === 'object' && !Array.isArray(object))
      return object as Record<string, unknown>;
  } catch {
    // JSON and type errors both indicate an invalid command argument.
  }
  throw new ConsoleCommandError(
    'invalidArgument',
    `${argument.name}: ${value}`,
  );
}

function parseArray(argument: Argument, value: string): unknown[] {
  try {
    const items: unknown = JSON.parse(value);
    if (Array.isArray(items)) return items;
  } catch {
    // JSON and type errors both indicate an invalid command argument.
  }
  throw new ConsoleCommandError('invalidArgument', argument.name);
}

export function parseValue(
  argument: Argument,
  value: string,
  previous: ArgumentValue | undefined,
  token: string,
): ArgumentValue {
  if (
    (argument.choices && !argument.choices.includes(value)) ||
    (argument.pattern && !argument.pattern.test(value))
  )
    throw new ConsoleCommandError(
      'invalidArgument',
      `${argument.name}: ${value}`,
    );
  if (argument.kind === 'uri-list' || argument.kind === 'uri-or-list') {
    const uris = readList(value);
    const invalid = uris.find((uri) =>
      argument.kind === 'uri-list'
        ? !/^viking:\/\/.+/.test(uri)
        : !uri.startsWith('viking://'),
    );
    if (invalid !== undefined)
      throw new ConsoleCommandError('expectedUri', invalid);
    const items = mergeList(previous, uris);
    return argument.kind === 'uri-or-list' && items.length === 1
      ? items[0]!
      : items;
  }
  if (argument.kind === 'integer-list')
    return mergeList(previous, readIntegerList(argument, value));
  if (previous !== undefined)
    throw new ConsoleCommandError('duplicateArgument', token);
  switch (argument.kind) {
    case 'uri':
      if (!value.startsWith('viking://'))
        throw new ConsoleCommandError('expectedUri', value);
      return value;
    case 'integer':
    case 'number':
      return parseNumber(argument, value, argument.kind === 'integer');
    case 'boolean':
    case 'boolean-or-json':
      if (/^(?:true|false)$/i.test(value))
        return value.toLowerCase() === 'true';
      if (argument.kind === 'boolean-or-json')
        return parseObject(argument, value);
      throw new ConsoleCommandError(
        'invalidArgument',
        `${argument.name}: ${value}`,
      );
    case 'json-object':
      return parseObject(argument, value);
    case 'json-array':
      return parseArray(argument, value);
    default:
      return value;
  }
}
