// Compatibility only. Product policy and response classification have explicit owners.
export {
  identifyServiceProvider,
  resolveConfiguredServiceProvider,
} from '#/platform/connection/service-provider'
export type {
  ServiceProvider,
  ServiceSelection,
} from '#/platform/studio-contract'
export {
  STUDIO_FEATURES,
  isStudioFeatureVisible,
} from '#/platform/access/features'
export type { StudioDomain, CapabilityState } from '#/platform/access/features'
