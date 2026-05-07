#!/usr/bin/env node
import { PACKAGE_NAME } from "@openviking/copilot-shared";

export function buildServerInfo(): { name: string; sharedFrom: string } {
  return {
    name: "openviking-copilot-mcp",
    sharedFrom: PACKAGE_NAME,
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const info = buildServerInfo();
  process.stdout.write(`${info.name} (scaffold) wired to ${info.sharedFrom}\n`);
}
