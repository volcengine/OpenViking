/** Public authentication interface; no Provider or workbench dependency. */
export { loginAndActivate } from './lib/connection-operations'
export type { ConnectionLoginResult } from './lib/connection-operations'
export {
  StudioSetupError,
  StudioSetupRequestError,
} from './lib/connection-errors'
