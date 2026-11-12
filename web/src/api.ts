import type {
  BatchStatus,
  DiscoverResult,
  DomainStat,
  FetchOptions,
  FetchResult,
  Health,
  Job,
  Lookup,
  SearchHit,
  SitesResponse,
} from './types'

const BASE = ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init)
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* keep default */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function startFetch(url: string, options: FetchOptions) {
  return request<{ job_id: string; url: string; status: string }>('/api/fetch', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ url, ...options }),
  })
}

export async function getJob(jobId: string) {
  return request<Job>(`/api/fetch/${jobId}`)
}

export function markdownUrl(jobId: string) {
  return `/api/fetch/${jobId}/markdown`
}

export function articleMarkdownUrl(articleId: string) {
  return `/api/articles/${articleId}/markdown`
}

export async function getArticle(articleId: string): Promise<FetchResult> {
  return request<FetchResult>(`/api/articles/${articleId}`)
}

export async function searchSites(query: string, limit = 60) {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return request<SitesResponse>(`/api/sites?${params.toString()}`)
}

export async function lookup(url: string) {
  const params = new URLSearchParams({ url })
  return request<Lookup>(`/api/lookup?${params.toString()}`)
}

export async function health() {
  return request<Health>('/api/health')
}

/** Poll a job until it settles. Resolves with the finished job. */
export async function waitForJob(
  jobId: string,
  onUpdate: (job: Job) => void,
  signal?: AbortSignal,
): Promise<Job> {
  const deadline = Date.now() + 4 * 60 * 1000
  let delay = 320

  for (;;) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const job = await getJob(jobId)
    onUpdate(job)
    if (job.status === 'done' || job.status === 'error') return job
    if (Date.now() > deadline) throw new Error('Timed out waiting for the fetch to finish.')
    await new Promise((resolve) => setTimeout(resolve, delay))
    delay = Math.min(delay + 60, 900)
  }
}

export function proxiedImage(src: string, articleUrl: string) {
  if (!/^https?:\/\//i.test(src)) return src
  const params = new URLSearchParams({ url: src, ref: articleUrl })
  return `/api/image?${params.toString()}`
}

export async function startBatch(urls: string[], options: FetchOptions) {
  return request<{ id: string; count: number; skipped: Array<{ url: string; error: string }> }>(
    '/api/batch',
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ urls, ...options }),
    },
  )
}

export async function getBatch(id: string) {
  return request<BatchStatus>(`/api/batch/${id}`)
}

export async function discover(site: string, since = '7d', limit = 20) {
  const params = new URLSearchParams({ site, since, limit: String(limit) })
  return request<DiscoverResult>(`/api/discover?${params.toString()}`)
}

export async function searchLibrary(q: string, limit = 20) {
  const params = new URLSearchParams({ q, limit: String(limit) })
  return request<{ query: string; count: number; results: SearchHit[] }>(`/api/search?${params.toString()}`)
}

export async function domainStats(limit = 40) {
  return request<{ domains: DomainStat[]; tracked: number; articles_stored: number }>(
    `/api/stats?limit=${limit}`,
  )
}

export async function updateSiteTable(force = false) {
  return request<Record<string, unknown>>(`/api/sites/update?force=${force ? 'true' : 'false'}`, {
    method: 'POST',
  })
}

export function epubUrl(articleId: string) {
  return `/api/articles/${articleId}/epub`
}

export function pdfUrl(articleId: string) {
  return `/api/articles/${articleId}/pdf`
}
