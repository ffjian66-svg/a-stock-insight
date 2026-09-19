import { describe, expect, it } from 'vitest'
import { priceSourceNote } from './Common'

/**
 * `priceSourceNote` 的全部意义就是 `close` 与 `is_stale` 的**优先级**：盘后回退到
 * 收盘价时 `is_stale` 也是 true，读成「报价延迟」会让人以为存在一个被延迟的实时价，
 * 而屏上其实就是一个收盘价。所以这几条断言钉的是顺序，不是文案好不好看。
 */
describe('priceSourceNote', () => {
  it('回退到收盘价时说「收盘」并带上日期，而不是「报价延迟」', () => {
    expect(priceSourceNote({ price_source: 'close', price_date: '2026-09-16', is_stale: true })).toBe(
      '收盘 09-16',
    )
  })

  it('收盘价但缺日期时也要说「收盘」，不能退回「报价延迟」', () => {
    expect(priceSourceNote({ price_source: 'close', price_date: null, is_stale: true })).toBe('收盘价')
  })

  it('有实时报价时按 is_stale 说「报价延迟」，不说「收盘」', () => {
    expect(priceSourceNote({ price_source: 'quote', price_date: null, is_stale: true })).toBe('报价延迟')
    expect(priceSourceNote({ price_source: 'quote', price_date: null, is_stale: false })).toBe('')
  })

  it('既无报价也无日线时说「无行情」', () => {
    expect(priceSourceNote({ price_source: 'none', is_stale: true })).toBe('无行情')
  })

  it('老响应缺 price_source 字段时退回 is_stale 口径，不崩', () => {
    expect(priceSourceNote({ is_stale: true })).toBe('报价延迟')
    expect(priceSourceNote({})).toBe('')
  })
})
