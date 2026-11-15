import memories from './assets/step-1.webp';
import sessions from './assets/step-2.webp';
import personalResources from './assets/step-3.webp';
import sharedResources from './assets/step-4.webp';
import experiences from './assets/step-5.webp';
import peers from './assets/step-6.webp';
import type { ReactNode } from 'react';

function artwork(src: string, width: number, height: number) {
  return (
    <img
      className="ov-directory-tour__artwork"
      src={src}
      alt=""
      style={{ width, height }}
    />
  );
}

/** Illustrations bundled with the component; hosts can override or pass null. */
export const tourIllustrations: ReadonlyMap<string, ReactNode> = new Map([
  ['userMemories', artwork(memories, 212, 70)],
  ['userSessions', artwork(sessions, 216, 72)],
  ['userResources', artwork(personalResources, 223, 72)],
  ['sharedResources', artwork(sharedResources, 216, 72)],
  ['userExperiences', artwork(experiences, 216, 72)],
  ['userPeers', artwork(peers, 252, 84)],
]);
