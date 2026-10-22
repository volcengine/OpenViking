/**
 * The console Host component.
 *
 * This bundle mounts it as its own `openviking-console` Cordis row beside the
 * memory runtime, so Plugin Manager shows two components under one plugin. The
 * package's `dsh.client` provides the sidebar UI; the plugin master switch
 * unloads the Host rows and the browser half together, while this row toggles
 * only the console's fenced status route and Studio proxy.
 */
import { OpenVikingClient } from "./client.mjs";
import { resolveConfig } from "./config.mjs";
import { mountConsole } from "./console.mjs";

export const name = "openviking-console";
export const inject = ["webServer", "connection"];

export function apply(ctx, input = {}) {
  const config = resolveConfig(input);
  mountConsole(ctx, config, new OpenVikingClient(config));
}
