# Third-Party Notices

Vika is licensed under the GNU Affero General Public License v3.0; see
[LICENSE](./LICENSE). The source tree and the release archives also contain the
third-party software below. Each part stays under its own license.

| Component | Where | License |
|---|---|---|
| Node.js (version in `package.json` `vika.node`) | release: `node/` | MIT, with the licenses of its bundled parts in `node/LICENSE` |
| npm production dependencies | release: `node_modules/` | each package's own license, kept in its directory |
| pm2 and @pm2/agent, run as a separate process manager | release: `node_modules/pm2`, `node_modules/@pm2/agent` | AGPL-3.0 |
| lark-cli binary ([larksuite/cli](https://github.com/larksuite/cli)) | release: `node_modules/@larksuite/cli/bin/lark-cli` | MIT |
| Slack CLI ([slackapi/slack-cli](https://github.com/slackapi/slack-cli)) | release: `bin/slack`, license in `bin/slack.LICENSE` | Apache-2.0 |
| Weixin protocol code derived from [Tencent/openclaw-weixin](https://github.com/Tencent/openclaw-weixin) | source: `packages/daemon/src/weixin/` | MIT; see `NOTICE.md` and `LICENSE.openclaw-weixin` there |
| Claude Code stream-json adapter derived from [ZaynJarvis/zouk-daemon](https://github.com/ZaynJarvis/zouk-daemon) 0.6.0 | source: `packages/daemon/src/claude.mjs` | MIT |
| Pi durable harness ([earendil-works/pi](https://github.com/earendil-works/pi), `@earendil-works/pi-durable`, `pi-ai`, `chord`) | release: `node_modules/@earendil-works/` | MIT |

`npm ls --omit=dev --all` lists the exact npm dependency tree of a checkout.
