import type { RequestModel } from './types'

type PhaseWindow = RequestModel['selection']

function datePart(value: unknown): string | null {
  const text = String(value ?? '')
  const match = text.match(/^\d{4}-\d{2}(?:-\d{2})?/)
  if (!match) return null
  return match[0].length === 7 ? `${match[0]}-01` : match[0]
}

function inWindow(value: string, window: PhaseWindow): boolean {
  return value >= window.start && value <= window.end
}

export function phaseDividerMarkLine(
  axisValues: string[],
  request: Pick<RequestModel, 'selection' | 'validation'>,
  axisCategories: string[] = axisValues,
) {
  const normalized = axisValues.map(datePart)
  const hasSelection = normalized.some(value => value && inWindow(value, request.selection))
  const validationIndex = normalized.findIndex(value => value && inWindow(value, request.validation))
  if (!hasSelection || validationIndex <= 0 || !axisCategories[validationIndex]) return undefined

  return {
    symbol: ['none', 'none'],
    silent: true,
    lineStyle: { type: 'dashed', color: '#f0bd72', width: 2 },
    label: { show: false },
    data: [{ xAxis: axisCategories[validationIndex] }],
  }
}
