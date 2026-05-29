import type { OVClient } from "./client.js";
import type { OVConfig } from "./config.js";

export class IndexBuilder {
  private client: OVClient;
  private config: OVConfig;
  private cachedIndex = "";

  constructor(client: OVClient, config: OVConfig) {
    this.client = client;
    this.config = config;
  }

  async buildIndex(): Promise<string> {
    if (!this.client.connected) return "";

    const sections: string[] = ["## OpenViking Knowledge Index"];

    try {
      // 1. User memories
      const memUri = await this.client.resolveTargetUri("viking://user/memories");
      const memEntries = await this.client.ls(memUri);
      const memAbstracts: string[] = [];
      for (const entry of memEntries.slice(0, 20)) {
        if (!entry.isDir) {
          const abs = await this.client.abstract(
            `${memUri}/${entry.name}`,
          );
          if (abs) memAbstracts.push(abs);
        }
      }
      sections.push(`### ${memUri} (${memEntries.length} entries)`);
      for (const a of memAbstracts.slice(0, 10)) {
        sections.push(`- ${a}`);
      }
      if (memAbstracts.length > 10) {
        sections.push(`- (${memAbstracts.length - 10} more — use viking_search)`);
      }

      // 2. Resources
      const resEntries = await this.client.ls("viking://resources/");
      if (resEntries.length > 0) {
        sections.push(`### viking://resources/ (${resEntries.length} resources)`);
        for (const e of resEntries.slice(0, 5)) {
          sections.push(`- ${e.name}`);
        }
      }
    } catch {
      // Graceful degradation — return what we have
    }

    // 3. Tools advertisement
    sections.push(
      "Tools: viking_search | viking_read | viking_browse | viking_remember | viking_forget | viking_add_resource | viking_archive_expand",
    );

    const index = sections.join("\n");

    // Token budget enforcement
    let tokens = 0;
    let cjk = 0;
    for (let i = 0; i < index.length; i++) {
      if (index.charCodeAt(i) >= 0x3000) cjk++;
    }
    tokens = Math.ceil(cjk * 1.5 + (index.length - cjk) / 4);

    if (tokens > this.config.indexBudget) {
      // Truncate to fit
      this.cachedIndex = index.slice(0, this.config.indexBudget * 3);
    } else {
      this.cachedIndex = index;
    }

    return this.cachedIndex;
  }

  getIndex(): string {
    return this.cachedIndex;
  }
}
