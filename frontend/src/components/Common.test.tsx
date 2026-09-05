import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Change, Score } from './Common'

describe('行情展示组件', () => {
  it('为上涨数值显示正号和百分比', () => {
    expect(renderToStaticMarkup(<Change value={2.36} />)).toContain('+2.36%')
  })

  it('缺失评分时显示占位符', () => {
    expect(renderToStaticMarkup(<Score value={null} />)).toContain('--')
  })
})
