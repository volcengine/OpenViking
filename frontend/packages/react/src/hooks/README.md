# Internal Hooks

Hooks are implementation details kept beside their component or module. Hooks shared by actual consumers belong in `src/shared/`. The package does not expose `@openviking/react/hooks` or headless business Hook APIs.

Data-loading Hooks stay in modules. Component-private Hooks manage local UI only and know no APIs.
