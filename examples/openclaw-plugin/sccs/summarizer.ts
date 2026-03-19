const DEFAULT_SUMMARY_MAX_CHARS = 300;
type JsonValue = null | boolean | number | string | JsonValue[] | { [k: string]: JsonValue };

export class SummaryExtractor {
  private maxChars: number;
  constructor(maxChars?: number) {
    this.maxChars = maxChars ?? DEFAULT_SUMMARY_MAX_CHARS;
  }
  truncate(text: string, maxChars?: number): string {
    const cleaned = text.replace(/\s+/g, " ").trim();
    const limit = maxChars ?? this.maxChars;
    if (cleaned.length <= limit) return cleaned;
    if (limit <= 1) return cleaned.slice(0, limit);
    return `${cleaned.slice(0, limit - 1)}…`;
  }
  summarize(content: string, enable = true): string {
    if (!enable) return this.truncate(content);
    try {
      return this.summarizeJson(JSON.parse(content) as JsonValue);
    } catch {
      if (this.detectMarkdown(content)) return this.summarizeMarkdown(content);
      const delim = this.detectTable(content);
      if (delim) return this.summarizeTable(content, delim);
      if (this.detectLog(content)) return this.summarizeLog(content);
      if (this.detectCode(content)) return this.summarizeCode(content);
      return this.summarizeText(content);
    }
  }
  private summarizeJson(data: JsonValue): string {
    if (Array.isArray(data)) {
      const types = new Map<string, number>();
      data.slice(0, 30).forEach((item) => {
        const t = Array.isArray(item) ? "array" : item === null ? "null" : typeof item;
        types.set(t, (types.get(t) ?? 0) + 1);
      });
      const typePart = Array.from(types.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([t, c]) => `${t}:${c}`)
        .join(",");
      const first = data[0];
      const firstPart =
        first && typeof first === "object" && !Array.isArray(first)
          ? `first_keys=${Object.keys(first).slice(0, 6).join(",")}`
          : `first=${this.truncate(String(first ?? ""), 40)}`;
      return this.truncate(`JSON array: items=${data.length} types=${typePart} ${firstPart}`);
    }
    if (data && typeof data === "object") {
      const keys = Object.keys(data);
      const fields: string[] = [];
      keys.slice(0, 6).forEach((k) => {
        const v = (data as Record<string, JsonValue>)[k];
        if (Array.isArray(v)) fields.push(`${k}[${v.length}]`);
        else if (v && typeof v === "object") fields.push(`${k}{${Object.keys(v).length}}`);
        else if (typeof v === "string") fields.push(`${k}='${this.truncate(v, 20)}'`);
        else fields.push(`${k}=${String(v)}`);
      });
      return this.truncate(
        `JSON object: keys=${keys.length}[${keys.slice(0, 8).join(",")}] sample=${fields.join(";")}`,
      );
    }
    return this.truncate(String(data));
  }
  private extractKeywords(content: string, limit = 4): string[] {
    const words = content.toLowerCase().match(/[A-Za-z_][A-Za-z0-9_]{2,}/g) ?? [];
    const stop = new Set([
      "the","and","for","with","from","that","this","have","has","are","was","were","but","you","your","not",
      "can","will","all","any","get","set","out","too","use","using","into","when","where","none","true","false",
      "null","json","line","file","path","http","https","info","debug","warning","error",
    ]);
    const freq = new Map<string, number>();
    for (const w of words) {
      if (w.length < 4 || stop.has(w)) continue;
      freq.set(w, (freq.get(w) ?? 0) + 1);
    }
    return Array.from(freq.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit)
      .map(([k]) => k);
  }
  private extractKeyLines(lines: string[], limit = 2): string[] {
    const score = (line: string) => {
      const upper = line.toUpperCase();
      let s = 0;
      if (/ERROR|EXCEPTION|TRACEBACK|FATAL|FAILED/.test(upper)) s += 6;
      if (/WARN/.test(upper)) s += 4;
      if (/\b[45]\d{2}\b/.test(line)) s += 3;
      if (/\b(GET|POST|PUT|DELETE|PATCH)\b/.test(upper)) s += 2;
      if (/[A-Za-z_][A-Za-z0-9_]*\(/.test(line)) s += 2;
      if (/(\/[A-Za-z0-9._-]+){2,}|[A-Za-z]:\\/.test(line)) s += 2;
      s += Math.min(line.length, 120) / 30;
      return s;
    };
    const candidates = lines.map((l) => l.trim()).filter(Boolean);
    const ranked = candidates.sort((a, b) => score(b) - score(a));
    const out: string[] = [];
    const seen = new Set<string>();
    for (const line of ranked) {
      const compact = this.truncate(line, 60);
      if (seen.has(compact)) continue;
      seen.add(compact);
      out.push(compact);
      if (out.length >= limit) break;
    }
    return out;
  }
  private summarizeText(content: string): string {
    const lines = content.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const head = `len=${content.length},line=${lines.length},E=${(content.match(/\bERROR\b|\bEXCEPTION\b|\bTRACEBACK\b/gi) ?? []).length},W=${(content.match(/\bWARN(?:ING)?\b/gi) ?? []).length},url=${(content.match(/https?:\/\/\S+/gi) ?? []).length},path=${(content.match(/(?:[A-Za-z]:\\|\/)[A-Za-z0-9._\\/\-]+/g) ?? []).length},num=${(content.match(/\b\d+(?:\.\d+)?\b/g) ?? []).length}`;
    const keywords = this.extractKeywords(content);
    const first = this.truncate(lines[0] ?? content, 55);
    const keyLines = this.extractKeyLines(lines, 2);
    const parts = [head];
    if (keywords.length) parts.push(`kw=${keywords.join(",")}`);
    parts.push(`first=${first}`);
    if (keyLines.length) parts.push(`key=${keyLines.join(" | ")}`);
    return this.truncate(parts.join(" ; "));
  }
  private detectLog(content: string): boolean {
    const lines = content.split(/\r?\n/).filter((l) => l.trim());
    if (lines.length < 2) return false;
    const levelHits = lines.filter((l) => /\b(INFO|DEBUG|WARN|WARNING|ERROR|TRACE|FATAL)\b/.test(l)).length;
    const tsHits = lines.filter((l) => /\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/.test(l)).length;
    return levelHits >= 2 || tsHits >= 2;
  }
  private summarizeLog(content: string): string {
    const lines = content.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const levels = {
      E: lines.filter((l) => /\b(ERROR|FATAL)\b/.test(l)).length,
      W: lines.filter((l) => /\bWARN(?:ING)?\b/.test(l)).length,
      I: lines.filter((l) => /\bINFO\b/.test(l)).length,
    };
    const ts = lines
      .map((l) => l.match(/\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/)?.[0])
      .filter(Boolean) as string[];
    const errTypes = (content.match(/\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\b/g) ?? []).slice(0, 2);
    const keyLines = this.extractKeyLines(lines, 2);
    const parts = [`log lines=${lines.length} levels=E${levels.E} W${levels.W} I${levels.I}`];
    if (ts.length) parts.push(`ts=${ts[0]}..${ts[ts.length - 1]}`);
    if (errTypes.length) parts.push(`err=${errTypes.join(",")}`);
    if (keyLines.length) parts.push(`key=${keyLines.join(" | ")}`);
    return this.truncate(parts.join(" ; "));
  }
  private detectMarkdown(content: string): boolean {
    const lines = content.split(/\r?\n/);
    const headings = lines.filter((l) => /^\s*#{1,6}\s+\S+/.test(l)).length;
    const lists = lines.filter((l) => /^\s*([-*+]|\d+\.)\s+\S+/.test(l)).length;
    const fences = lines.filter((l) => /^\s*```/.test(l)).length;
    const links = (content.match(/\[[^\]]+\]\([^)]+\)/g) ?? []).length;
    return headings >= 1 && (lists >= 2 || fences >= 1 || links >= 1);
  }
  private summarizeMarkdown(content: string): string {
    const lines = content.split(/\r?\n/);
    const headings = lines
      .filter((l) => /^\s*#{1,6}\s+\S+/.test(l))
      .map((l) => l.replace(/^\s*#{1,6}\s+/, "").trim());
    const fences = lines
      .map((l) => l.match(/^\s*```([A-Za-z0-9_+-]+)?\s*$/)?.[1] ?? "")
      .filter(Boolean);
    const listItems = lines.filter((l) => /^\s*([-*+]|\d+\.)\s+\S+/.test(l)).length;
    const linkCount = (content.match(/\[[^\]]+\]\([^)]+\)/g) ?? []).length;
    const parts = [`md lines=${lines.length} headings=${headings.length}`];
    if (headings.length) {
      parts.push(`h=[${headings.slice(0, 2).map((h) => this.truncate(h, 18)).join(",")}]`);
    }
    if (fences.length) {
      parts.push(`code=${fences.length}[${fences.slice(0, 2).join(",")}]`);
    }
    if (listItems) parts.push(`lists=${listItems}`);
    if (linkCount) parts.push(`links=${linkCount}`);
    return this.truncate(parts.join(" ; "));
  }
  private detectTable(content: string): "," | "\t" | null {
    const lines = content.split(/\r?\n/).filter((l) => l.trim());
    if (lines.length < 2) return null;
    const delim = lines[0].includes("\t") ? "\t" : lines[0].includes(",") ? "," : null;
    if (!delim) return null;
    const cols = lines[0].split(delim).length;
    const similar = lines.slice(1, 4).filter((l) => l.split(delim).length === cols).length;
    return similar >= 1 ? delim : null;
  }
  private summarizeTable(content: string, delim: "," | "\t"): string {
    const lines = content.split(/\r?\n/).filter((l) => l.trim());
    const header = lines[0].split(delim).map((c) => c.trim()).filter(Boolean);
    const rows = Math.max(0, lines.length - 1);
    return this.truncate(`table rows=${rows} cols=${header.length} header=[${header.slice(0, 4).join(",")}]`);
  }
  private detectCode(content: string): boolean {
    const lines = content.split(/\r?\n/);
    const braces = lines.filter((l) => /[{};]/.test(l)).length;
    const defs = lines.filter((l) => /\b(class|def|function|const|let|var|import|from)\b/.test(l)).length;
    return braces >= 4 || defs >= 3;
  }
  private summarizeCode(content: string): string {
    const lines = content.split(/\r?\n/).filter((l) => l.trim());
    const funcs = (content.match(/\b([A-Za-z_][A-Za-z0-9_]*)\s*\(/g) ?? []).slice(0, 4);
    const imports = (content.match(/^\s*(import|from)\b.*$/gm) ?? []).slice(0, 2);
    const parts = [`code lines=${lines.length}`];
    if (funcs.length) {
      parts.push(`funcs=${funcs.map((f) => f.replace(/\s*\(/, "")).join(",")}`);
    }
    if (imports.length) {
      parts.push(`imports=${imports.map((l) => this.truncate(l.trim(), 40)).join(" | ")}`);
    }
    return this.truncate(parts.join(" ; "));
  }
}
