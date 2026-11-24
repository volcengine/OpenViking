/** Complete only the current argument; never execute or expand shell input. */
export function compileSuggestions(
  input: string,
  directories: string[],
  skills: string[],
): string[] {
  if (/^(?:ov\s+)?\/?comp\w*$/.test(input)) return ['compile --from ']
  if (!/^(?:ov\s+)?\/?compile\s/.test(input)) return []
  const argument = input.match(/^(.*\s)(--from|--to|--skill)\s+([^\s]*)$/)
  if (argument) {
    const candidates = argument[2] === '--skill' ? skills : directories
    return [...new Set(candidates)]
      .filter((uri) => uri.toLowerCase().includes(argument[3].toLowerCase()))
      .slice(0, 8)
      .map((uri) => `${argument[1]}${argument[2]} ${JSON.stringify(uri)} `)
  }
  const flag = input.match(/^(.*\s)(--[\w-]*)?$/)
  return flag
    ? ['--from', '--to', '--skill', '--instruction', '--args']
        .filter((value) => value.startsWith(flag[2] || ''))
        .map((value) => `${flag[1]}${value} `)
    : []
}
