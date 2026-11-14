import { useEffect, useState } from 'react'
import { assetUrl, getAsset } from '../api/assets'
import type { StoredAsset } from '../types'

export function DocumentAttachment({ assetId }: { assetId: string }) {
  const [file, setFile] = useState<StoredAsset | null>(null)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState(false)

  useEffect(() => {
    let active = true
    setFile(null)
    setError('')
    setPreview(false)
    getAsset(assetId).then(
      (value) => { if (active) setFile(value) },
      (reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : 'File unavailable.') },
    )
    return () => { active = false }
  }, [assetId])

  if (error) return <p role="status" className="text-xs text-muted">{error}</p>
  if (!file) return <p role="status" className="text-xs text-muted">Loading file…</p>

  const isPdf = file.media_type === 'application/pdf'
  const url = assetUrl(assetId)
  return (
    <section aria-label={`File: ${file.filename}`} className="my-2 w-full min-w-0 overflow-hidden rounded-xl border border-line-bright bg-surface-2">
      <div className="flex flex-wrap items-center gap-3 p-3">
        <span className="rounded border border-line px-2 py-1 font-mono text-xs text-accent">{isPdf ? 'PDF' : 'FILE'}</span>
        <div className="min-w-0 flex-1">
          <p className="break-words text-sm font-medium">{file.filename}</p>
          <p className="text-xs text-muted">{Math.max(1, Math.ceil(file.size_bytes / 1024))} KB</p>
        </div>
        {isPdf && (
          <button type="button" aria-expanded={preview} onClick={() => setPreview(!preview)} className="rounded border border-line-bright px-3 py-1.5 text-xs hover:bg-line">
            {preview ? 'Close preview' : 'Preview'}
          </button>
        )}
        <a href={url} download={file.filename} className="rounded bg-accent px-3 py-1.5 text-xs text-ink">Download</a>
      </div>
      {preview && (
        <div className="border-t border-line">
          <iframe title={`Preview of ${file.filename}`} src={`${url}?preview=true`} className="h-[65vh] min-h-80 w-full bg-white" />
          <p className="p-3 text-xs text-muted">If your browser cannot display this PDF, <a href={`${url}?preview=true`} target="_blank" rel="noreferrer" className="text-accent underline">open it in a new tab</a> or download it.</p>
        </div>
      )}
    </section>
  )
}

// Basic inline Markdown the model writes (**bold**, *italic* / _italic_, `code`). Rendered as React elements,
// never as HTML: anything else stays literal text.
const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+?\*\*)|(__[^_\n]+?__)|(?<![\w*])(\*[^*\s][^*\n]*?\*)(?![\w*])|(?<![\w_])(_[^_\s][^_\n]*?_)(?![\w_])/g

export function InlineMarkdown({ text }: { text: string }) {
  const out = []
  let offset = 0
  for (const match of text.matchAll(INLINE)) {
    const index = match.index ?? 0
    if (index > offset) out.push(text.slice(offset, index))
    const token = match[0]
    if (match[1]) {
      out.push(<code key={index} className="rounded bg-black/5 px-1 font-mono text-[0.9em]">{token.slice(1, -1)}</code>)
    } else if (match[2] || match[3]) {
      out.push(<strong key={index} className="font-semibold">{token.slice(2, -2)}</strong>)
    } else {
      out.push(<em key={index}>{token.slice(1, -1)}</em>)
    }
    offset = index + token.length
  }
  if (offset < text.length) out.push(text.slice(offset))
  return <>{out}</>
}

/** Render only known file links and Google editor links, plus basic inline Markdown; never inject model HTML. */
export function ChatContent({ content, markdown = true }: { content: string; markdown?: boolean }) {
  const text = (value: string) => (markdown ? <InlineMarkdown text={value} /> : value)
  const matches = [...content.matchAll(/\[([^\]\n]*)\]\((\/api\/assets\/([a-f0-9]{16})\/raw|https:\/\/(?:docs|drive)\.google\.com\/[^\s)]+)\)/g)]
  const parts = []
  let offset = 0
  for (const match of matches) {
    const index = match.index ?? 0
    parts.push(<span key={`text-${index}`} className="whitespace-pre-wrap">{text(content.slice(offset, index))}</span>)
    parts.push(match[3]
      ? <DocumentAttachment key={`file-${index}`} assetId={match[3]} />
      : <a key={`link-${index}`} href={match[2]} target="_blank" rel="noreferrer" className="text-accent underline">{match[1]}</a>)
    offset = index + match[0].length
  }
  parts.push(<span key="end" className="whitespace-pre-wrap">{text(content.slice(offset))}</span>)
  return <div className="min-w-0 w-full">{parts}</div>
}
