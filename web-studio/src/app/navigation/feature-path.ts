import { STUDIO_FEATURES } from '#/platform/access/features'

export function featureForPath(pathname: string) {
  return STUDIO_FEATURES.find(
    (f) => pathname === f.to || pathname.startsWith(`${f.to}/`),
  )
}
