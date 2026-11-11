import type { FetchOptions, Health, Job, Lookup, SitesResponse } from './types'

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
