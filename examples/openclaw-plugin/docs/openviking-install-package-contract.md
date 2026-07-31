# OpenViking Install And Package Contract

This document records the install/package contract that the OpenViking OpenClaw plugin must keep stable.

It intentionally documents the current package contract instead of the unpublished TOS shell-script flow from #2613. Current main does not contain those TOS publishing scripts, so the safe contract is the npm/openclaw package shape that is actually built and installed by this repository.

## Required Package Entries

The plugin package must include:

| Entry | Why it is required |
| --- | --- |
| `dist/` | Compiled runtime loaded by OpenClaw. |
| `dist/index.js` | Main extension entry. |
| `dist/commands/setup.js` | Setup entry used by OpenClaw. |
| `package.json` | OpenClaw package metadata and runtime dependencies. |
| `openclaw.plugin.json` | Plugin manifest. |
| `install-manifest.json` | Install-time file contract. |
| `README.md`, `INSTALL.md`, `INSTALL-ZH.md` | User-facing install and usage docs. |
| `skills/` | Packaged OpenViking skills. |

Helper modules added under `plugin/` must be listed in the package `files` contract or included by a package-level directory rule.

## OpenClaw Metadata

`package.json` must keep these OpenClaw fields valid:

```json
{
  "openclaw": {
    "extensions": ["./dist/index.js"],
    "setupEntry": "./dist/commands/setup.js"
  }
}
```

The `setupEntry` must point at compiled JavaScript, not source TypeScript.

## Runtime Dependencies

Runtime dependencies must include everything the compiled plugin imports at runtime. Development-only test/build packages belong in `devDependencies`.

The package currently keeps an `axios` override to avoid installing vulnerable or incompatible transitive versions through OpenClaw packaging.

## Install Manifest

`install-manifest.json` defines the source files and metadata required by the OpenClaw install flow. When a new runtime source module is imported by `index.ts` or setup code, add it to `files.required`.

Required entries should include core runtime modules such as:

- `index.ts`
- `config.ts`
- `context-engine.ts`
- `client.ts`
- `auto-recall.ts`
- `recall-trace.ts`
- `commands/setup.ts`
- `package.json`
- `openclaw.plugin.json`

Optional entries are for files that improve the installed plugin but are not needed to load the runtime, such as lockfiles or extra docs.

## Verification Checklist

Before publishing or merging package-contract changes, run:

```bash
npm test -- tests/ut/package-install-contract.test.ts
npm run typecheck
npm run build
git diff --check
```

The package contract test should verify:

- compiled extension entry exists after build,
- compiled setup entry exists after build,
- `openclaw.extensions` and `openclaw.setupEntry` point to `dist/*.js`,
- package `files` include runtime assets and docs,
- install manifest required files exist,
- runtime dependencies and overrides are present.

## Out Of Scope

The #2613 branch also contained draft TOS publishing/install notes. Those notes depend on release scripts and object-storage upload flow that are not present in current main. They should be added only with the corresponding scripts and CI/release process, not as standalone documentation.
