export type FormattedLiveResult = {
  isError: boolean
  text: string
}

export function formatLiveContextResult(value: unknown): FormattedLiveResult {
  if (typeof value === 'string') return { isError: false, text: value }
  if (!value || typeof value !== 'object') {
    return { isError: false, text: JSON.stringify(value) }
  }

  const result = value as Record<string, unknown>
  const content = Array.isArray(result.content) ? result.content : []
  const blocks = content.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const block = item as Record<string, unknown>
    if (block.type === 'text' && typeof block.text === 'string') {
      return [block.text]
    }
    return [JSON.stringify(block, null, 2)]
  })

  if (blocks.length) {
    return {
      isError: result.isError === true,
      text: blocks.join('\n\n'),
    }
  }

  const structured = result.structuredContent ?? result.structured_content
  return {
    isError: result.isError === true,
    text: JSON.stringify(structured ?? value, null, 2),
  }
}
