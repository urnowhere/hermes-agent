import { describe, expect, it } from 'vitest'

import { visualRowNav } from '../components/textInput.js'

describe('visualRowNav', () => {
  // ── Hard-newline cases (parity with lineNav) ──────────────────────────

  it('returns null for single-line input that fits in one row (up)', () => {
    expect(visualRowNav('hello', 3, -1, 80)).toBeNull()
  })

  it('returns null for single-line input that fits in one row (down)', () => {
    expect(visualRowNav('hello', 3, 1, 80)).toBeNull()
  })

  it('moves up across a hard newline preserving column', () => {
    // "hello\nworld", cols=80 → two visual rows, both under 80 chars
    // cursor at offset 9 = col 3 of "world" → col 3 of "hello" = offset 3
    expect(visualRowNav('hello\nworld', 9, -1, 80)).toBe(3)
  })

  it('moves down across a hard newline preserving column', () => {
    // cursor at offset 2 = col 2 of "hello" → col 2 of "world" = offset 8
    expect(visualRowNav('hello\nworld', 2, 1, 80)).toBe(8)
  })

  it('clamps to end of shorter destination line on up', () => {
    // "abc\nlong long text" — cursor at col 10 of line 1 → clamp to end of "abc"
    expect(visualRowNav('abc\nlong long text', 14, -1, 80)).toBe(3)
  })

  it('clamps to end of shorter destination line on down', () => {
    // "long long text\nabc" — cursor at col 10 → clamp to end of "abc"
    expect(visualRowNav('long long text\nabc', 10, 1, 80)).toBe(18)
  })

  it('returns null when on first line going up', () => {
    expect(visualRowNav('one\ntwo\nthree', 2, -1, 80)).toBeNull()
  })

  it('returns null when on last line going down', () => {
    expect(visualRowNav('one\ntwo\nthree', 10, 1, 80)).toBeNull()
  })

  // ── Soft-wrap cases (the new behavior) ────────────────────────────────

  it('navigates up within a soft-wrapped line', () => {
    // "abcdefghij" with cols=5 → visual rows: "abcde" (row 0), "fghij" (row 1)
    // cursor at offset 7 = 'h' → visual col 2 on row 1
    // Soft-wrap drift compensation: adjusted col = 2 + (-1) = 1 → offset 1
    expect(visualRowNav('abcdefghij', 7, -1, 5)).toBe(1)
  })

  it('navigates down within a soft-wrapped line', () => {
    // "abcdefghij" with cols=5 → visual rows: "abcde" (row 0), "fghij" (row 1)
    // cursor at offset 2 = 'c' → visual col 2 on row 0
    // Soft-wrap drift compensation: adjusted col = 2 + 1 = 3 → offset 8
    expect(visualRowNav('abcdefghij', 2, 1, 5)).toBe(8)
  })

  it('returns null going up from first visual row of a soft-wrapped line', () => {
    // "abcdefghij" with cols=5 → cursor on row 0 → up returns null (history)
    expect(visualRowNav('abcdefghij', 2, -1, 5)).toBeNull()
  })

  it('returns null going down from last visual row of a soft-wrapped line', () => {
    // "abcdefgh" with cols=5 → rows: "abcde" (row 0), "fgh" (row 1)
    // cursor at offset 6 on row 1 → down returns null (no row 2)
    expect(visualRowNav('abcdefgh', 6, 1, 5)).toBeNull()
  })

  it('clamps column when soft-wrapped destination row is shorter', () => {
    // "abcdefgh" with cols=5 → rows: "abcde" (row 0, 5 chars), "fgh" (row 1, 3 chars)
    // cursor at offset 4 = 'e' → visual col 4 on row 0 → row 1 only has cols 0-2
    // should clamp to end of row 1 = offset 8 (end of string)
    expect(visualRowNav('abcdefgh', 4, 1, 5)).toBe(8)
  })

  // ── Mixed: hard newlines + soft wraps ─────────────────────────────────

  it('navigates down from a soft-wrapped row into a hard-newline row', () => {
    // "abcdefghij\nxy" with cols=5
    // visual rows: "abcde" (row 0), "fghij" (row 1), "xy" (row 2)
    // cursor at offset 7 = 'h' → visual col 2 on row 1
    // srcDrift=1 (row 1 within "abcdefghij"), dstDrift=0 (row 0 within "xy")
    // compensation = -1, adjustedCol = 1 → offset 12 ('y')
    expect(visualRowNav('abcdefghij\nxy', 7, 1, 5)).toBe(12)
  })

  it('navigates up from a hard-newline row into a soft-wrapped row', () => {
    // "abcdefghij\nxy" with cols=5
    // visual rows: "abcde" (row 0), "fghij" (row 1), "xy" (row 2)
    // cursor at offset 11 = 'x' → visual col 0 on row 2
    // srcDrift=0 (row 0 within "xy"), dstDrift=1 (row 1 within "abcdefghij")
    // compensation = +1, adjustedCol = 1 → offset 6 ('g')
    expect(visualRowNav('abcdefghij\nxy', 11, -1, 5)).toBe(6)
  })

  it('navigates across multiple soft-wrapped rows step by step', () => {
    // "abcdefghijklmno" with cols=5
    // visual rows: "abcde" (row 0), "fghij" (row 1), "klmno" (row 2)
    // cursor at offset 12 = 'm' → visual col 2, adjusted col 1 → offset 6
    expect(visualRowNav('abcdefghijklmno', 12, -1, 5)).toBe(6)
    // then from offset 6 = 'g' → visual col 1, adjusted col 0 → offset 0
    expect(visualRowNav('abcdefghijklmno', 6, -1, 5)).toBe(0)
  })

  // ── Edge cases ────────────────────────────────────────────────────────

  it('handles empty string', () => {
    expect(visualRowNav('', 0, -1, 80)).toBeNull()
    expect(visualRowNav('', 0, 1, 80)).toBeNull()
  })

  it('handles cols=1 (every character is its own visual row)', () => {
    // "abc" with cols=1 → rows: "a" (row 0), "b" (row 1), "c" (row 2)
    // cursor at offset 1 = 'b' → up → offset 0
    expect(visualRowNav('abc', 1, -1, 1)).toBe(0)
    // cursor at offset 1 = 'b' → down → offset 2
    expect(visualRowNav('abc', 1, 1, 1)).toBe(2)
  })

  it('handles cursor at end of string on a soft-wrap boundary', () => {
    // "abcde" with cols=5 → row 0 = "abcde", cursor at offset 5 (end)
    // cursorLayout puts this on row 1 col 0 (trailing overflow)
    // down from row 1 → null (no row 2)
    expect(visualRowNav('abcde', 5, 1, 5)).toBeNull()
    // up from row 1 → row 0 col 0 = offset 0
    expect(visualRowNav('abcde', 5, -1, 5)).toBe(0)
  })
})
