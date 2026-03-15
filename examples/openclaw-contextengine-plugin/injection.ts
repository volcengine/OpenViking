type MemoryItem = {
  uri: string;
  content?: string;
  score?: number;
};

type PromptSections = {
  profile?: string;
  toolMemory?: string;
  ovCliGuidance?: string;
};

export function truncateToMaxChars(text: string, maxChars: number): string {
  if (maxChars <= 0) {
    return "";
  }
  if (text.length <= maxChars) {
    return text;
  }
  return text.slice(0, maxChars);
}

export function buildSystemPromptAddition(sections: PromptSections): string {
  const blocks: string[] = [];
  if (sections.profile?.trim()) {
    blocks.push(sections.profile.trim());
  }
  if (sections.toolMemory?.trim()) {
    blocks.push(sections.toolMemory.trim());
  }
  if (sections.ovCliGuidance?.trim()) {
    blocks.push(sections.ovCliGuidance.trim());
  }
  return blocks.join("\n\n");
}

export function buildSimulatedToolResultInjection(memories: MemoryItem[]): string {
  const lines: string[] = ["OpenViking retrieval results:"];

  for (const memory of memories) {
    const score = typeof memory.score === "number" ? ` score=${memory.score}` : "";
    lines.push(`- uri=${memory.uri}${score}`);
    if (memory.content?.trim()) {
      lines.push(`  content=${memory.content.trim()}`);
    }
  }

  return lines.join("\n");
}
