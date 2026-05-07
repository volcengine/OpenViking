import { PACKAGE_NAME } from "@openviking/copilot-shared";

export function activate(): { ready: true; shared: typeof PACKAGE_NAME } {
  return { ready: true, shared: PACKAGE_NAME };
}

export function deactivate(): void {}
