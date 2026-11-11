type CommandTemplate = {
  insertText: string
  usage: string
}

/** Show syntax after a selected command without putting placeholders in its value. */
export function getCommandInputHint(
  value: string,
  templates: CommandTemplate[],
): string {
  const template = [...templates]
    .sort((a, b) => b.insertText.length - a.insertText.length)
    .find(
      (item) =>
        value === item.insertText || value === item.insertText.trimEnd(),
    )
  if (!template) return ''

  const commandPrefix = template.insertText.trimEnd()
  if (!template.usage.startsWith(commandPrefix)) return ''
  const suffix = template.usage.slice(commandPrefix.length)
  if (!suffix.trim()) return ''
  return value.endsWith(' ') ? suffix.trimStart() : suffix
}
