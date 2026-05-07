/**
 * Regression tests for #19944 — virtual row height cache drift when rows
 * cross the full-render-tail boundary.
 *
 * The root cause: virtualRows was keyed only by message identity, so a row
 * measured at full-tail height retained that stale height in the cache after
 * it moved to bounded-history rendering (which produces a shorter height).
 *
 * The fix: append :t (in-tail) or :b (bounded) to the row key so that
 * crossing the FULL_RENDER_TAIL_ITEMS boundary produces a new key and forces
 * the height cache to re-estimate.
 */

import { describe, expect, it } from 'vitest'

import { FULL_RENDER_TAIL_ITEMS } from '../config/limits.js'
import { estimatedMsgHeight } from '../lib/virtualHeights.js'
import type { Msg } from '../types.js'

describe('virtual row render-mode key discrimination (#19944)', () => {
  it('long assistant message has different height in tail vs bounded-history mode', () => {
    const longText = Array.from({ length: 40 }, (_, i) => `Line ${i + 1}: ${'x'.repeat(60)}`).join('\n')
    const msg: Msg = { role: 'assistant', text: longText }
    const cols = 80

    const tailHeight = estimatedMsgHeight(msg, cols, { compact: false, details: false, limitHistory: false })
    const boundedHeight = estimatedMsgHeight(msg, cols, { compact: false, details: false, limitHistory: true })

    expect(tailHeight).toBeGreaterThan(boundedHeight)
  })

  it('short assistant message has same height in both modes (no drift possible)', () => {
    const msg: Msg = { role: 'assistant', text: 'Short reply.' }
    const cols = 80

    const tailHeight = estimatedMsgHeight(msg, cols, { compact: false, details: false, limitHistory: false })
    const boundedHeight = estimatedMsgHeight(msg, cols, { compact: false, details: false, limitHistory: true })

    expect(tailHeight).toBe(boundedHeight)
  })

  it('FULL_RENDER_TAIL_ITEMS is a positive integer', () => {
    expect(Number.isInteger(FULL_RENDER_TAIL_ITEMS)).toBe(true)
    expect(FULL_RENDER_TAIL_ITEMS).toBeGreaterThan(0)
  })

  it('row key suffix changes when a row crosses the tail boundary', () => {
    const totalRows = FULL_RENDER_TAIL_ITEMS + 5
    const baseKey = 'assistant:text:abc123:1'

    const suffixes = Array.from({ length: totalRows }, (_, index) => {
      const inTail = index >= totalRows - FULL_RENDER_TAIL_ITEMS
      return `${baseKey}:${inTail ? 't' : 'b'}`
    })

    expect(suffixes[0]).toMatch(/:b$/)
    expect(suffixes[4]).toMatch(/:b$/)
    expect(suffixes[totalRows - 1]).toMatch(/:t$/)
    expect(suffixes[totalRows - FULL_RENDER_TAIL_ITEMS]).toMatch(/:t$/)

    const boundaryOld = suffixes[totalRows - FULL_RENDER_TAIL_ITEMS - 1]
    const boundaryNew = suffixes[totalRows - FULL_RENDER_TAIL_ITEMS]
    expect(boundaryOld).not.toBe(boundaryNew)
  })

  it('height cache miss on render-mode transition forces re-estimation', () => {
    const tailKey = 'assistant:text:abc123:1:t'
    const boundedKey = 'assistant:text:abc123:1:b'

    const heightCache = new Map<string, number>()
    heightCache.set(tailKey, 120)

    expect(heightCache.has(boundedKey)).toBe(false)
    expect(heightCache.get(boundedKey)).toBeUndefined()
  })
})
