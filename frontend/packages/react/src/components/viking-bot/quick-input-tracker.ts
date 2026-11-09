interface PromptRange {
  from: number;
  to: number;
}

/** Map one content edit, using the editor's post-edit caret to disambiguate repeats. */
function editRange(before: string, after: string, caret?: number) {
  let prefix = 0;
  while (
    prefix < before.length &&
    prefix < after.length &&
    before[prefix] === after[prefix]
  )
    prefix++;
  let suffix = 0;
  while (
    suffix < before.length &&
    suffix < after.length &&
    before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  )
    suffix++;
  const delta = after.length - before.length;
  if (prefix + suffix <= Math.min(before.length, after.length))
    return { from: prefix, to: before.length - suffix, delta };
  if (caret !== undefined) {
    const oldEnd = caret - delta;
    if (
      oldEnd >= 0 &&
      oldEnd <= before.length &&
      caret >= 0 &&
      caret <= after.length &&
      before.slice(oldEnd) === after.slice(caret)
    )
      return { from: Math.min(prefix, oldEnd, caret), to: oldEnd, delta };
  }
  // Overlapping prefix/suffix admits multiple edit locations. Never guess ownership.
  return undefined;
}

/** Instance-local ownership metadata; never locates a prompt by matching its text. */
export function createQuickInputTracker(initial: string) {
  let text = initial;
  let range: PromptRange | undefined;
  const edit = (next: string, caret?: number) => {
    if (text === next) return;
    const change = editRange(text, next, caret);
    if (range) {
      if (!change) range = undefined;
      else if (change.to <= range.from)
        range = {
          from: range.from + change.delta,
          to: range.to + change.delta,
        };
      else if (change.from < range.to) range = undefined;
    }
    text = next;
  };
  return {
    edit,
    release() {
      range = undefined;
    },
    insert(value: string, prompt: string) {
      edit(value);
      const prefix = range
        ? text.slice(0, range.from)
        : text.trim()
          ? `${text}\n`
          : '';
      const suffix = range ? text.slice(range.to) : '';
      text = prefix + prompt + suffix;
      range = { from: prefix.length, to: prefix.length + prompt.length };
      return text;
    },
  };
}
