import { createHmac, timingSafeEqual } from 'crypto';

const CHALLENGE_PATTERN = /^[0-9a-f]{64}$/;

export function isValidChallenge(challenge: unknown): challenge is string {
  return typeof challenge === 'string' && CHALLENGE_PATTERN.test(challenge);
}

export function createAuthProof(token: string, challenge: string): string {
  return createHmac('sha256', token).update(challenge).digest('hex');
}

export function tokenMatches(candidate: unknown, expected: string): boolean {
  if (typeof candidate !== 'string') return false;
  const candidateBytes = Buffer.from(candidate);
  const expectedBytes = Buffer.from(expected);
  return (
    candidateBytes.length === expectedBytes.length &&
    timingSafeEqual(candidateBytes, expectedBytes)
  );
}
