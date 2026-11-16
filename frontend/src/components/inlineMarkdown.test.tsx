import { describe, expect, test } from 'bun:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { ChatContent } from './DocumentAttachment'

const html = (content: string, markdown = true) => renderToStaticMarkup(<ChatContent content={content} markdown={markdown} />)

describe('ChatContent', () => {
  test('renders basic emphasis and code as elements', () => {
    const out = html('Your project is **Lantern Finch**, *nearly* done; run `bun test`.')
    expect(out).toContain('<strong class="font-semibold">Lantern Finch</strong>')
    expect(out).toContain('<em>nearly</em>')
    expect(out).toContain('>bun test</code>')
    expect(out).not.toContain('**')
  })

  test('never injects model HTML', () => {
    const out = html('**<img src=x onerror=alert(1)>** <script>x</script>')
    expect(out).not.toContain('<img')
    expect(out).not.toContain('<script')
    expect(out).toContain('&lt;img')
  })

  test('leaves ordinary asterisks and snake_case alone', () => {
    expect(html('2 * 3 * 4 and my_file_name')).toContain('2 * 3 * 4 and my_file_name')
  })

  test('keeps file and editor links working', () => {
    const out = html('See [the doc](https://docs.google.com/document/d/abc) for **details**.')
    expect(out).toContain('href="https://docs.google.com/document/d/abc"')
    expect(out).toContain('<strong class="font-semibold">details</strong>')
  })

  test('user text stays literal', () => {
    expect(html('I said **this**', false)).toContain('I said **this**')
  })
})
