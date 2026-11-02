// Compatibility exports for existing callers. New composition lives in platform/deployment.
export {
  getStudioDeployment,
  resolveStudioDeployment,
} from '#/platform/deployment/config'
export { isStudioConnectionAllowed } from '#/platform/deployment/experience'
export type { StudioDeploymentConfig } from '#/platform/deployment/types'
export type { StudioEdition } from '#/platform/studio-contract'
