import { ConsoleCommandError } from './command-messages.js';
import { parseValue } from './command-values.js';

interface CommandToken {
  value: string;
  literal: boolean;
}

/** Parses arguments as data; never invokes a shell. */
export function tokenize(input: string): CommandToken[] {
  const tokens: CommandToken[] = [];
  let value = '',
    quote = '',
    escaped = false,
    started = false,
    literal = false;
  for (const character of input.trim()) {
    if (escaped) {
      value += character;
      escaped = false;
      started = true;
    } else if (character === '\\' && quote !== "'") {
      escaped = true;
      literal ||= !/^--?[\w-]+=/.test(value);
    } else if (quote) {
      if (character === quote) quote = '';
      else value += character;
    } else if (character === '"' || character === "'") {
      quote = character;
      started = true;
      literal ||= !/^--?[\w-]+=/.test(value);
    } else if (/\s/.test(character)) {
      if (started) tokens.push({ value, literal });
      value = '';
      started = false;
      literal = false;
    } else {
      if ('|;&<>`$'.includes(character))
        throw new ConsoleCommandError('shellSyntax');
      value += character;
      started = true;
    }
  }
  if (quote || escaped) throw new ConsoleCommandError('unclosedQuote');
  if (started) tokens.push({ value, literal });
  if (tokens[0]?.value === 'ov') tokens.shift();
  if (tokens[0]?.value.startsWith('/'))
    tokens[0].value = tokens[0].value.slice(1);
  return tokens;
}

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
