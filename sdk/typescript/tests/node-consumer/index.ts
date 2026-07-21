import { OpenVikingClient } from "../../dist/index.js";

export const client = new OpenVikingClient({
  baseUrl: "https://example.com",
});

// @ts-expect-error the server only accepts local or shared temporary uploads.
new OpenVikingClient({ baseUrl: "https://example.com", uploadMode: "proxy" });

client.getSkill("demo", { targetUri: "viking://agent/skills" });
// @ts-expect-error getSkill accepts one concrete skill root, not multiple roots.
client.getSkill("demo", { targetUri: ["viking://agent/skills"] });

client.exportOVPack("viking://resources/docs", "./docs.ovpack");
client.importOVPack("./docs.ovpack", "viking://resources", {
  onConflict: "overwrite",
  vectorMode: "auto",
});
// @ts-expect-error OVPack conflict policies are restricted by the server.
client.restoreOVPack("./docs.ovpack", { onConflict: "replace" });
client.gitCommit({ message: "snapshot", branch: "main" });
client.relations("viking://resources/docs");
