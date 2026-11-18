import Prism from 'prismjs';

// Fish shares shell strings, variables and comments with Bash, but uses its
// own block keywords and function declarations without parentheses.
Prism.languages.fish = Prism.languages.extend('bash', {
  keyword:
    /\b(?:and|begin|break|builtin|case|command|continue|else|end|exec|for|function|if|in|not|or|return|set|status|switch|while)\b/,
  'function-name': {
    pattern: /(\bfunction[\t ]+)[\w-]+/,
    lookbehind: true,
    alias: 'function',
  },
});
