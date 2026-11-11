export type JobStatus = 'queued' | 'running' | 'done' | 'error'

export interface Step {
  stage: string
  status: string
  label: string
  at: number
}

export interface Attempt {
  stage: string
  label: string
  ok: boolean
  status: number
  chars: number
  paywalled: boolean
  ms: number
  note: string
}

export interface Article {
  url: string
  title: string
  author: string
  date: string
  site: string
  markdown: string
  text: string
  word_count: number
  reading_minutes: number
  excerpt: string
  images: string[]
}

export interface StrategyInfo {
  domain: string
  name: string
  bypass_type: string
  supported: boolean
}

export interface FetchResult {
  article: Article
  via: string
  strategy: StrategyInfo
  attempts: Attempt[]
  fetched_at: string
  elapsed_ms: number
  confidence: string
  cached?: boolean
}

export interface Job {
  id: string
  url: string
  status: JobStatus
  steps: Step[]
  result: FetchResult | null
  error: { code: string; message: string } | null
  created_at: string
  finished_at: string
}

export interface SiteRow {
  domain: string
  name: string
  bypass_type: string
}

export interface SitesResponse {
  total: number
  supported_total: number
  sites: SiteRow[]
}

export interface Health {
  ok: boolean
  version: string
  sites_supported: number
  playwright_ready: boolean
  uptime_seconds: number
}

export interface Lookup {
  url: string
  supported: boolean
  domain: string
  name: string
  bypass_type: string
  needs_browser?: boolean
}

export interface FetchOptions {
  force_browser: boolean
  skip_browser: boolean
  use_archive: boolean
  include_images: boolean
}

export interface HistoryEntry {
  url: string
  title: string
  site: string
  via: string
  at: number
  words: number
}
