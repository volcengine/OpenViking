import { ConsoleCommandError } from './command-messages.js';
import { parseValue } from './command-values.js';

import { tokenize, type CommandToken } from './command-tokenizer.js';

export { tokenize } from './command-tokenizer.js';

export interface Argument {
  name: string;
  kind?:
    | 'uri'
    | 'uri-list'
    | 'uri-or-list'
    | 'integer'
    | 'integer-list'
    | 'number'
    | 'boolean'
    | 'boolean-or-json'
    | 'json-array'
    | 'json-object';
  placeholder?: string;
  descriptionKey?: string;
  optional?: boolean;
  rest?: boolean;
  required?: boolean;
  alias?: string;
  aliases?: readonly string[];
  hidden?: boolean;
  valueKey?: string;
  defaultValue?: string | number | boolean;
  helpDefault?: Argument['defaultValue'];
  choices?: readonly string[];
  pattern?: RegExp;
  min?: number;
  max?: number;
}

export type ArgumentValue =
  string | number | boolean | unknown[] | Record<string, unknown>;

export type ArgumentValues = Record<string, ArgumentValue>;

function isOption(token: CommandToken) {
  return !token.literal && /^(?:--|-[A-Za-z])/.test(token.value);
}

function flagValue(
  argument: Argument,
  inline: string | undefined,
  next: CommandToken | undefined,
  token: string,
): [value: string, consumed: number] {
  if (inline !== undefined) return [inline, 0];
  if (argument.kind === 'boolean' || argument.kind === 'boolean-or-json') {
    if (next && /^(?:true|false)$/i.test(next.value)) return [next.value, 1];
    if (argument.kind === 'boolean-or-json' && next?.value.startsWith('{'))
      return [next.value, 1];
    return ['true', 0];
  }
  if (!next || isOption(next))
    throw new ConsoleCommandError('missingValue', token);
  return [next.value, 1];
}

export function parseArguments(
  tokens: readonly CommandToken[],
  positional: readonly Argument[],
  flags: readonly Argument[] = [],
  onArgument?: (
    argument: Argument,
    value: string,
    start: number,
    end: number,
  ) => void,
) {
  const result: ArgumentValues = {};
  const rest: string[] = [];
  let index = 0;
  let options = true;
  for (let cursor = 0; cursor < tokens.length; cursor++) {
    const current = tokens[cursor]!;
    const token = current.value;
    if (options && !current.literal && token === '--') {
      options = false;
      continue;
    }
    const flag = options && isOption(current);
    const equals = flag ? token.indexOf('=') : -1;
    const name = equals === -1 ? token : token.slice(0, equals);
    const argument = flag
      ? flags.find(
          (item) =>
            name === `--${item.name}` ||
            name === item.alias ||
            item.aliases?.includes(name),
        )
      : positional[index];
    if (!argument) throw new ConsoleCommandError('unexpectedArgument', token);
    if (!flag) {
      if (argument.rest) {
        rest.push(token);
        continue;
      }
      index++;
    }
    const key = argument.valueKey ?? argument.name;
    const [raw, consumed] = flag
      ? flagValue(
          argument,
          equals === -1 ? undefined : token.slice(equals + 1),
          tokens[cursor + 1],
          name,
        )
      : [token, 0];
    if (onArgument) {
      const valueToken = tokens[cursor + consumed]!;
      const valueStart =
        equals === -1 ? valueToken.start : valueToken.start + equals + 1;
      onArgument(argument, raw, valueStart, valueToken.end);
    }
    cursor += consumed;
    result[key] = parseValue(argument, raw, result[key], name);
  }
  const text = positional[index];
  if (text?.rest && rest.length)
    result[text.valueKey ?? text.name] = parseValue(
      text,
      rest.join(' '),
      undefined,
      text.name,
    );
  for (const argument of positional)
    applyDefault(argument, result, !argument.optional, argument.name);
  for (const flag of flags)
    applyDefault(flag, result, flag.required === true, `--${flag.name}`);
  return result;
}

function applyDefault(
  argument: Argument,
  result: ArgumentValues,
  required: boolean,
  name: string,
) {
  const key = argument.valueKey ?? argument.name;
  if (result[key] !== undefined) return;
  if (required) throw new ConsoleCommandError('missingArgument', name);
  if (argument.defaultValue !== undefined) result[key] = argument.defaultValue;
}

export function clearRequested(input: string) {
  try {
    const tokens = tokenize(input);
    if (tokens[0]?.value !== 'clear') return false;
    parseArguments(tokens.slice(1), []);
    return true;
  } catch {
    return false;
  }
}
