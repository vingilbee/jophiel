import { describe, expect, test } from 'bun:test'
import { anchorFromHash } from './format'

describe('anchorFromHash', () => {
  test('reads a plain or encoded fragment', () => {
    expect(anchorFromHash('#lessons')).toBe('lessons')
    expect(anchorFromHash('#open%20questions')).toBe('open questions')
  })

  test('no fragment names nothing', () => {
    expect(anchorFromHash('')).toBeNull()
    expect(anchorFromHash('#')).toBeNull()
  })

  test('a malformed fragment is ignored instead of throwing', () => {
    expect(anchorFromHash('#%')).toBeNull()
    expect(anchorFromHash('#%E0%A4%A')).toBeNull()
  })

  test('quotes come back as text, for the caller to escape', () => {
    expect(anchorFromHash('#a%22b')).toBe('a"b')
  })
})
