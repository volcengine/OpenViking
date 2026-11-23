import memories from './assets/step-1.webp';
import sessions from './assets/step-2.webp';
import personalResources from './assets/step-3.webp';
import sharedResources from './assets/step-4.webp';
import experiences from './assets/step-5.webp';
import peers from './assets/step-6.webp';
import memoriesDark from './assets/step-1-dark.webp';
import sessionsDark from './assets/step-2-dark.webp';
import personalResourcesDark from './assets/step-3-dark.webp';
import sharedResourcesDark from './assets/step-4-dark.webp';
import experiencesDark from './assets/step-5-dark.webp';
import peersDark from './assets/step-6-dark.webp';
import type { ReactNode } from 'react';

function artwork(src: string, darkSrc: string, width: number, height: number) {
  return (
    <>
      <img
        className="ov-directory-tour__artwork ov-directory-tour__artwork--light"
        src={src}
        alt=""
        style={{ width, height }}
      />
      <img
        className="ov-directory-tour__artwork ov-directory-tour__artwork--dark"
        src={darkSrc}
        alt=""
        style={{ width, height }}
      />
    </>
  );
}

/** Illustrations bundled with the component; hosts can override or pass null. */
export const tourIllustrations: ReadonlyMap<string, ReactNode> = new Map([
  ['userMemories', artwork(memories, memoriesDark, 212, 70)],
  ['userSessions', artwork(sessions, sessionsDark, 216, 72)],
  ['userResources', artwork(personalResources, personalResourcesDark, 223, 72)],
  ['sharedResources', artwork(sharedResources, sharedResourcesDark, 216, 72)],
  ['userExperiences', artwork(experiences, experiencesDark, 216, 72)],
  ['userPeers', artwork(peers, peersDark, 252, 84)],
]);
