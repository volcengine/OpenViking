import { OpenVikingClient } from "../../dist/index.js";

export const client = new OpenVikingClient({
  baseUrl: "https://example.com",
});

// @ts-expect-error the server only accepts local or shared temporary uploads.
new OpenVikingClient({ baseUrl: "https://example.com", uploadMode: "proxy" });

client.getSkill("demo", { targetUri: "viking://agent/skills" });
// @ts-expect-error getSkill accepts one concrete skill root, not multiple roots.
client.getSkill("demo", { targetUri: ["viking://agent/skills"] });
