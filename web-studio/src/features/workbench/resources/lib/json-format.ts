export interface JsonFormatRequest {
  content: string
}

export interface JsonFormatResponse {
  content: string
  error: string | null
}

export function formatJsonContent(content: string): string {
  // Validate as before, then format the original tokens so numbers stay exact.
  JSON.parse(content)
  const tokens = content.match(/"(?:\\.|[^"\\])*"|[{}[\],:]|[^ \t\r\n"{}[\],:]+/g) ?? []
  let depth = 0

  return tokens
    .map((token, index) => {
      const previous = tokens[index - 1]
      const afterOpening = previous === '{' || previous === '['
      const closing = token === '}' || token === ']'
      if (closing) depth -= 1

      let prefix = ''
      if (previous === ':') {
        prefix = ' '
      } else if (
        previous === ',' ||
        (afterOpening && !closing) ||
        (closing && !afterOpening)
      ) {
        prefix = '\n' + '  '.repeat(depth)
      }

      if (token === '{' || token === '[') depth += 1
      return prefix + token
    })
    .join('')
}
