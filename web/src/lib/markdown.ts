import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { proxiedImage } from '../api'

marked.setOptions({
  gfm: true,
  breaks: false,
})

/**
 * Markdown -> sanitised HTML, with article images routed through the API's
 * image proxy (many paywall CDNs 403 when the Referer isn't the publisher).
 */
export function renderMarkdown(
  markdown: string,
  articleUrl: string,
): { html: string; dropCap: boolean } {
  const raw = marked.parse(markdown, { async: false }) as string

  const clean = DOMPurify.sanitize(raw, {
    FORBID_TAGS: ['script', 'style', 'iframe', 'form', 'input', 'button'],
    FORBID_ATTR: ['onerror', 'onload', 'onclick', 'style'],
    ADD_ATTR: ['target', 'loading'],
  })

  const template = document.createElement('template')
  template.innerHTML = clean

  template.content.querySelectorAll('img').forEach((img) => {
    const src = img.getAttribute('src') ?? ''
    if (src) {
      img.setAttribute('data-direct-src', src)
      img.setAttribute('src', proxiedImage(src, articleUrl))
    }
    img.setAttribute('loading', 'lazy')
    img.setAttribute('decoding', 'async')
    if (!img.getAttribute('alt')) img.setAttribute('alt', '')
  })

  template.content.querySelectorAll('a[href]').forEach((anchor) => {
    anchor.setAttribute('target', '_blank')
    anchor.setAttribute('rel', 'noopener noreferrer')
  })

  // Drop the duplicate leading H1: the article header already shows the title.
  const first = template.content.firstElementChild
  if (first && first.tagName === 'H1') first.remove()

  // A drop cap only works on real prose — not on a one-line list or a label.
  const opening = template.content.firstElementChild
  const dropCap =
    opening?.tagName === 'P' && (opening.textContent ?? '').trim().length > 180

  return { html: template.innerHTML, dropCap }
}

export function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text)
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  document.execCommand('copy')
  area.remove()
  return Promise.resolve()
}
