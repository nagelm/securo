/**
 * Fork (owner 2026-10-15): a straight-line trend through past balances,
 * extended over the forecast, to compare against the forecast's own lines.
 *
 * Least squares over (days since the first point, value). Returns a function
 * giving the trend's value on any ISO date, or null with fewer than two points.
 */
export function historicTrend(points: { date: string; value: number }[]): ((iso: string) => number) | null {
  if (points.length < 2) return null
  const day = (iso: string) => Date.parse(iso + 'T00:00:00Z') / 86_400_000
  const x0 = day(points[0].date)
  const xs = points.map((p) => day(p.date) - x0)
  const ys = points.map((p) => p.value)
  const n = points.length
  const meanX = xs.reduce((a, b) => a + b, 0) / n
  const meanY = ys.reduce((a, b) => a + b, 0) / n
  let sxx = 0
  let sxy = 0
  for (let i = 0; i < n; i++) {
    sxx += (xs[i] - meanX) ** 2
    sxy += (xs[i] - meanX) * (ys[i] - meanY)
  }
  if (sxx === 0) return null
  const slope = sxy / sxx
  const intercept = meanY - slope * meanX
  return (iso: string) => Math.round((intercept + slope * (day(iso) - x0)) * 100) / 100
}
