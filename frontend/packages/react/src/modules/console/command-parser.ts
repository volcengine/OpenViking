import { ConsoleCommandError } from './command-messages.js';

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
      literal = true;
    } else if (quote) {
      if (character === quote) quote = '';
      else value += character;
    } else if (character === '"' || character === "'") {
      quote = character;
      started = true;
      literal = true;
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
  kind?: 'uri' | 'integer' | 'boolean';
  optional?: boolean;
  choices?: readonly string[];
  min?: number;
}

export function parseArguments(
  tokens: readonly CommandToken[],
  positional: readonly Argument[],
  flags: readonly Argument[] = [],
) {
  const result: Record<string, string | number | boolean> = {};
  let index = 0;
  let options = true;
  for (let cursor = 0; cursor < tokens.length; cursor++) {
    const current = tokens[cursor]!;
    const token = current.value;
    if (options && !current.literal && token === '--') {
      options = false;
      continue;
    }
    const flag = options && !current.literal && token.startsWith('--');
    const argument = flag
      ? flags.find((item) => token === `--${item.name}`)
      : positional[index++];
    if (!argument) throw new ConsoleCommandError('unexpectedArgument', token);
    if (Object.hasOwn(result, argument.name))
      throw new ConsoleCommandError('duplicateArgument', token);
    const next =
      flag && argument.kind !== 'boolean' ? tokens[++cursor] : current;
    if (
      !next ||
      (flag &&
        argument.kind !== 'boolean' &&
        !next.literal &&
        next.value.startsWith('--'))
    )
      throw new ConsoleCommandError('missingValue', token);
    const value = flag && argument.kind === 'boolean' ? 'true' : next.value;
    if (argument.kind === 'uri' && !/^viking:\/\//.test(value))
      throw new ConsoleCommandError('expectedUri', value);
    if (argument.choices && !argument.choices.includes(value))
      throw new ConsoleCommandError(
        'invalidArgument',
        `${argument.name}: ${value}`,
      );
    if (argument.kind === 'integer') {
      const number = Number(value);
      if (
        !/^-?\d+$/.test(value) ||
        !Number.isSafeInteger(number) ||
        number < (argument.min ?? 0)
      )
        throw new ConsoleCommandError(
          'invalidArgument',
          `${argument.name}: ${value}`,
        );
      result[argument.name] = number;
    } else result[argument.name] = argument.kind === 'boolean' ? true : value;
  }
  for (const argument of positional) {
    if (!argument.optional && result[argument.name] === undefined)
      throw new ConsoleCommandError('missingArgument', argument.name);
  }
  return result;
}
