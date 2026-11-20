import { ConsoleCommandError } from './command-messages.js';

export interface CommandToken {
  value: string;
  literal: boolean;
  start: number;
  end: number;
}

/** Parses arguments as data; never invokes a shell. */
export function tokenizeArguments(input: string): CommandToken[] {
  const tokens: CommandToken[] = [];
  let value = '';
  let quote = '';
  let escaped = false;
  let literal = false;
  let start = -1;
  for (let index = 0; index < input.length; index++) {
    const character = input[index]!;
    if (!escaped && !quote && /\s/.test(character)) {
      if (start !== -1) tokens.push({ value, literal, start, end: index });
      value = '';
      start = -1;
      literal = false;
      continue;
    }
    if (start === -1) start = index;
    if (escaped) {
      // Double quotes preserve backslashes before ordinary characters, such as \d.
      if (quote === '"' && !/["\\$`\n]/.test(character)) value += '\\';
      value += character;
      escaped = false;
    } else if (character === '\\' && quote !== "'") {
      escaped = true;
      literal ||= !/^--?[\w-]+=/.test(value);
    } else if (quote) {
      if (character === quote) quote = '';
      else value += character;
    } else if (character === '"' || character === "'") {
      quote = character;
      literal ||= !/^--?[\w-]+=/.test(value);
    } else {
      if ('|;&<>`$'.includes(character))
        throw new ConsoleCommandError('shellSyntax');
      value += character;
    }
  }
  if (quote || escaped) throw new ConsoleCommandError('unclosedQuote');
  if (start !== -1) tokens.push({ value, literal, start, end: input.length });
  return tokens;
}

export function tokenize(input: string): CommandToken[] {
  const tokens = tokenizeArguments(input.trim());
  if (tokens[0]?.value === 'ov') tokens.shift();
  if (tokens[0]?.value.startsWith('/'))
    tokens[0].value = tokens[0].value.slice(1);
  return tokens;
}
