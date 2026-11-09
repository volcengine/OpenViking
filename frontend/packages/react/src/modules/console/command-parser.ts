/** Parses arguments as data; never invokes a shell. */
export function tokenize(input: string): string[] {
  const tokens: string[] = [];
  let value = '',
    quote = '',
    escaped = false,
    started = false;
  for (const character of input.trim()) {
    if (escaped) {
      value += character;
      escaped = false;
      started = true;
    } else if (character === '\\' && quote !== "'") {
      escaped = true;
    } else if (quote) {
      if (character === quote) quote = '';
      else value += character;
    } else if (character === '"' || character === "'") {
      quote = character;
      started = true;
    } else if (/\s/.test(character)) {
      if (started) tokens.push(value);
      value = '';
      started = false;
    } else {
      if ('|;&<>`$'.includes(character))
        throw new Error('Shell syntax is not supported.');
      value += character;
      started = true;
    }
  }
  if (quote || escaped) throw new Error('Unclosed quote or escape.');
  if (started) tokens.push(value);
  if (tokens[0] === 'ov') tokens.shift();
  if (tokens[0]?.startsWith('/')) tokens[0] = tokens[0].slice(1);
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
  tokens: string[],
  positional: readonly Argument[],
  flags: readonly Argument[] = [],
) {
  const result: Record<string, string | number | boolean> = {};
  let index = 0;
  for (let cursor = 0; cursor < tokens.length; cursor++) {
    const token = tokens[cursor]!;
    const flag = token.startsWith('--');
    const argument = flag
      ? flags.find((item) => token === `--${item.name}`)
      : positional[index++];
    if (!argument) throw new Error(`Unexpected argument: ${token}`);
    if (Object.hasOwn(result, argument.name))
      throw new Error(`Duplicate argument: ${token}`);
    const value = flag
      ? argument.kind === 'boolean'
        ? 'true'
        : tokens[++cursor]
      : token;
    if (value === undefined || (flag && value.startsWith('--')))
      throw new Error(`Missing value: ${token}`);
    if (argument.kind === 'uri' && !/^viking:\/\//.test(value))
      throw new Error(`Expected a Viking URI: ${value}`);
    if (argument.choices && !argument.choices.includes(value))
      throw new Error(`Invalid ${argument.name}: ${value}`);
    if (argument.kind === 'integer') {
      const number = Number(value);
      if (
        !/^-?\d+$/.test(value) ||
        !Number.isSafeInteger(number) ||
        number < (argument.min ?? 0)
      )
        throw new Error(`Invalid ${argument.name}: ${value}`);
      result[argument.name] = number;
    } else result[argument.name] = argument.kind === 'boolean' ? true : value;
  }
  for (const argument of positional) {
    if (!argument.optional && result[argument.name] === undefined)
      throw new Error(`Missing ${argument.name}`);
  }
  return result;
}
