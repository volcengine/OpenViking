# OVSA Console

Read-only frontend for [RFC OVSA-CONSOLE-001](https://github.com/volcengine/OpenViking/discussions/7700), Slice A. It follows the Web Studio frontend stack: React, TypeScript, Vite, Lucide icons, and Vitest/Testing Library. The production bundle is served by the existing loopback-only Shared Web process.

## Included

- Instance health, Connector degradation, Runtime snapshot, Worker slots, and persisted metrics.
- Task filtering and stable `before` cursor pagination. Polling only affects the latest page.
- Task details, persisted inputs, Worker turns, reports, and task-linked Activity.
- Separate Worker report → Router inbox → final IM delivery states, including an explicit `UNCERTAIN` warning.
- Cognition Sessions, redacted Activity, and all capability `enabled / available / effective` states with reason codes.
- Responsive navigation and light/dark themes.

There are no cancel, retry, pause, Runtime switch, configuration, Provider, process, or secret controls. The frontend does not call mutation APIs. The Shared Web allowlist likewise proxies only the RFC Slice A GET routes.

## Legacy backend behavior

The current daemon can still expose only `/api/activity`, `/health`, and `/ready`. When v1 discovery returns 404, the console uses those existing GET endpoints for health, Connector, Session, and Activity observation. Task and metric panels show `workers_not_merged`; Runtime details that the old API cannot verify are unavailable. No task data is inferred from message activities.

The previous message-level timeline remains at `/activity`. The new console is served at `/` and `/console`.

## Develop

From `superagent/`:

```sh
npm install --include=dev
npm run dev --workspace @ov-superagent/console
```

Vite listens on port 5173 and proxies API requests to Shared Web on 3737. Start Shared Web separately with its normal instance configuration.

## Build, test, and browser evidence

```sh
npm run build:console
npm run test:console
npm run test:browser --workspace @ov-superagent/console
```

The build is committed under `packages/web/public/console/` because the release packager archives committed files rather than running a frontend build. Rebuild after changing console source.

Browser tests start the real Shared Web server with an isolated RFC fixture daemon. Fixtures are test-only and are never mounted in production. The test checks desktop/mobile flows, legacy capability boundaries, browser errors, and that every request is GET. Screenshots are written to `docs/console/`.

Install Chromium once if needed:

```sh
npx playwright install chromium
```
