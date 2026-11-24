// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0-only
/** Native CONFLICTS_CONTENT protocol. These IDs exist only in the view model. */
import type { WikiConflict as WikiFileConflictRecord } from '../../components/wiki-dashboard/types.js';
import {
  createMarkdownAnchorLocator,
  locateMarkdownAnchor as locateWikiConflict,
} from '../../shared/markdown/locate-markdown-anchor.js';
import { splitWikiMetadata } from './wiki-metadata.js';
export { locateWikiConflict };
export type WikiFileConflicts =
  | { status: 'available'; document: string; records: WikiFileConflictRecord[] }
  | { status: 'missing' | 'invalid' };
// Wire protocol sentinel must remain independent of the display locale.
const manualResolutionBasis = '\u4eba\u5de5\u4fee\u590d';
const timestamp = (value: unknown): value is number =>
  typeof value === 'number' &&
  Number.isSafeInteger(value) &&
  value >= 0 &&
  Number.isFinite(new Date(value * 1000).getTime());

export const parseWikiFileConflicts = (content: string): WikiFileConflicts => {
  const { document, payloads, complete } = splitWikiMetadata(content);
  if (payloads.length === 0) {
    return { status: 'missing' };
  }
  if (!complete) {
    return { status: 'invalid' };
  }
  const source = payloads.filter(Boolean).join(',');
  let parsed: unknown;
  try {
    parsed = source ? JSON.parse(`[${source}]`) : [];
  } catch {
    return { status: 'invalid' };
  }
  if (!Array.isArray(parsed)) {
    return { status: 'invalid' };
  }
  const locate = createMarkdownAnchorLocator(document);
  const records: WikiFileConflictRecord[] = [];
  for (const [index, raw] of parsed.entries()) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      return { status: 'invalid' };
    }
    const value = raw as Record<string, unknown>;
    if (
      typeof value.match_text !== 'string' ||
      !value.match_text ||
      typeof value.problem_description !== 'string' ||
      typeof value.reason !== 'string' ||
      !['high', 'medium', 'low'].includes(value.severity as string) ||
      !['unfixed', 'fixed'].includes(value.state as string) ||
      !timestamp(value.begin_time) ||
      typeof value.fix_plan !== 'string' ||
      typeof value.resolution_basis !== 'string'
    ) {
      return { status: 'invalid' };
    }
    if (
      value.state === 'fixed'
        ? !timestamp(value.fix_time) || value.fix_time < value.begin_time
        : value.fix_time !== null
    ) {
      return { status: 'invalid' };
    }
    const matchText = value.match_text;
    const location = locate(matchText);
    records.push({
      id: JSON.stringify([index, value.begin_time, matchText]),
      matchText,
      description: value.problem_description,
      reason: value.reason,
      severity: value.severity as WikiFileConflictRecord['severity'],
      state: value.state as WikiFileConflictRecord['state'],
      beginTime: value.begin_time,
      fixTime: value.fix_time as number | null,
      fixPlan: value.fix_plan,
      resolutionBasis: value.resolution_basis,
      resolutionSource:
        value.state === 'fixed' && value.resolution_basis.trim()
          ? value.resolution_basis.trim() === manualResolutionBasis
            ? 'manual'
            : 'automatic'
          : undefined,
      location,
    });
  }
  return { status: 'available', document, records };
};
