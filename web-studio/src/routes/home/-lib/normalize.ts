import {
  HEATMAP_COLOR_STOPS,
  HEATMAP_EMPTY_COLOR,
} from '../-constants/dashboard'
import type {
  ConsoleContextCommitItem,
  ConsoleTokenSeriesItem,
} from '@ov-server/api/v1/console'
import type { CommitHeatmapStats, HeatMapDayValue } from '../-types/dashboard'
import { asArray, asNumber, asRecord, asString } from './format'

export function normalizeTokenSeries(
  items: unknown,
): Array<Required<ConsoleTokenSeriesItem>> {
  return asArray(items).map((raw) => {
    const record = asRecord(raw)
    const vlmInput = asNumber(record.vlm_input)
    const vlmOutput = asNumber(record.vlm_output)
    const embeddingInput = asNumber(record.embedding_input)
    return {
      date: asString(record.date),
      embedding_input: embeddingInput,
      total: asNumber(record.total) || vlmInput + vlmOutput + embeddingInput,
      vlm_input: vlmInput,
      vlm_output: vlmOutput,
    }
  })
}

function percentile(sortedValues: number[], ratio: number): number {
  if (sortedValues.length === 0) return 0
  const index = Math.ceil(sortedValues.length * ratio) - 1
  return sortedValues[Math.max(0, Math.min(sortedValues.length - 1, index))]
}

export function buildHeatmapPanelColors(
  items: HeatMapDayValue[],
): Record<number, string> {
  const nonZeroCounts = Array.from(
    new Set(
      items
        .map((item) => item.count)
        .filter((count) => count > 0)
        .sort((a, b) => a - b),
    ),
  )

  if (nonZeroCounts.length === 0) {
    return { 0: HEATMAP_EMPTY_COLOR }
  }

  const thresholds = Array.from(
    new Set([
      Math.max(1, percentile(nonZeroCounts, 0.25)),
      Math.max(1, percentile(nonZeroCounts, 0.5)),
      Math.max(1, percentile(nonZeroCounts, 0.75)),
      Math.max(1, percentile(nonZeroCounts, 0.9)),
    ]),
  ).sort((a, b) => a - b)

  return thresholds.reduce<Record<number, string>>(
    (colors, threshold, index) => ({
      ...colors,
      [threshold]:
        HEATMAP_COLOR_STOPS[Math.min(index, HEATMAP_COLOR_STOPS.length - 1)],
    }),
    { 0: HEATMAP_EMPTY_COLOR },
  )
}

export function getHeatmapFillColor(
  count: number,
  panelColors: Record<number, string>,
): string {
  if (count <= 0) return 'var(--heatmap-empty)'

  const thresholds = Object.keys(panelColors)
    .map(Number)
    .filter((threshold) => threshold > 0)
    .sort((a, b) => a - b)

  const matched = thresholds.reduce<number | null>(
    (current, threshold) => (count >= threshold ? threshold : current),
    null,
  )

  return matched === null
    ? HEATMAP_COLOR_STOPS[0]
    : (panelColors[matched] ?? HEATMAP_COLOR_STOPS[0])
}

export function computeCommitHeatmapStats(
  items: HeatMapDayValue[],
): CommitHeatmapStats {
  return items.reduce<CommitHeatmapStats>(
    (stats, item) => {
      if (item.count <= 0) return stats

      return {
        activeDays: stats.activeDays + 1,
        peakCount: item.count > stats.peakCount ? item.count : stats.peakCount,
        peakDate: item.count > stats.peakCount ? item.date : stats.peakDate,
        recentDate: item.date > stats.recentDate ? item.date : stats.recentDate,
      }
    },
    {
      activeDays: 0,
      peakCount: 0,
      peakDate: '',
      recentDate: '',
    },
  )
}

export function normalizeCommitHeatmapData(items: unknown): HeatMapDayValue[] {
  const rowsByDate = new Map<string, Required<ConsoleContextCommitItem>>()

  for (const item of normalizeCommitItems(items)) {
    if (!item.date) continue

    const existing = rowsByDate.get(item.date) ?? {
      add_resource: 0,
      add_skill: 0,
      date: item.date,
      hour: 0,
      session_add_message: 0,
      session_commit: 0,
      total: 0,
    }

    rowsByDate.set(item.date, {
      add_resource: existing.add_resource + item.add_resource,
      add_skill: existing.add_skill + item.add_skill,
      date: item.date,
      hour: 0,
      session_add_message:
        existing.session_add_message + item.session_add_message,
      session_commit: existing.session_commit + item.session_commit,
      total: existing.total + item.total,
    })
  }

  return Array.from(rowsByDate.values())
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((item) => ({
      count: item.total,
      date: item.date,
      details: item,
    }))
}

export function normalizeCommitItems(
  items: unknown,
): Array<Required<ConsoleContextCommitItem>> {
  return asArray(items).map((raw) => {
    const record = asRecord(raw)
    const addResource = asNumber(record.add_resource)
    const addSkill = asNumber(record.add_skill)
    const sessionAddMessage = asNumber(record.session_add_message)
    const sessionCommit = asNumber(record.session_commit)
    return {
      add_resource: addResource,
      add_skill: addSkill,
      date: asString(record.date),
      hour: asNumber(record.hour),
      session_add_message: sessionAddMessage,
      session_commit: sessionCommit,
      total:
        asNumber(record.total) ||
        addResource + addSkill + sessionAddMessage + sessionCommit,
    }
  })
}
