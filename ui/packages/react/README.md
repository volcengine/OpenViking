# @openviking/ui

React UI modules for OpenViking applications. The initial scaffold exports `StatusBadge`; business modules have not been migrated.

This package is private during initial development and is not published to npm. It inherits the repository's AGPL-3.0-only license.

```tsx
import { StatusBadge } from '@openviking/ui';
import '@openviking/ui/style.css';

export default function Example() {
  return <StatusBadge status="ready">Connected</StatusBadge>;
}
```

React and ReactDOM are peer dependencies (`^18.2.0 || ^19.0.0`). Build output is ESM JavaScript, declarations, and an explicit stylesheet. Consumers must import the stylesheet once. Styles are scoped and do not reset application styles.

From `ui/`, run `npm run build:lib` to generate `packages/react/dist/`. See the [workspace README](../../README.md) for documentation development and deployment.
