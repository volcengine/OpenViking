export const TOKEN_SERIES_DAYS = 14
export const COMMIT_SERIES_DAYS = 365

export const TOKEN_COLORS = {
  embedding: 'oklch(0.5 0.11 252)',
  input: 'oklch(0.57 0.13 232)',
  output: 'oklch(0.62 0.12 188)',
}

export const HOME_ACCENT_COLORS = {
  icon: 'oklch(0.68 0.14 232)',
  iconSoft: 'oklch(0.68 0.14 232 / 0.14)',
}

export const HEATMAP_MONTH_LABELS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
]
export const HEATMAP_WEEK_LABELS = ['', 'Mon', '', 'Wed', '', 'Fri', '']
export const HEATMAP_COLOR_STOPS = [
  'oklch(0.82 0.07 232)',
  'oklch(0.7 0.1 232)',
  'oklch(0.58 0.13 238)',
  'oklch(0.46 0.13 245)',
] as const
export const HEATMAP_EMPTY_COLOR = 'oklch(0.92 0 0)'
