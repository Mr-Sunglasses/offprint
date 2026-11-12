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
  article_id?: string
  article: Article
  via: string
  strategy: StrategyInfo
  attempts: Attempt[]
  fetched_at: string
  elapsed_ms: number
  confidence: string
  paywalled_preview?: boolean
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
  articles_stored?: number
  sites_updated_at?: string
  sites_digest?: string
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
  proxy_fallback: boolean
}

export interface RecentArticle {
  article_id: string
  url: string
  title: string
  site: string
  word_count: number
  via: string
  fetched_at: string
  paywalled_preview: boolean
}

export interface HistoryEntry {
  url: string
  title: string
  site: string
  via: string
  at: number
  words: number
}

export interface BatchItem {
  url: string
  job_id: string
  status: string
  article_id?: string
  title?: string
  site?: string
  words?: number
  via?: string
  confidence?: string
  preview?: boolean
  cached?: boolean
  error?: string
  steps?: string
}

export interface BatchStatus {
  id: string
  created_at: string
  status: 'running' | 'done'
  total: number
  done: number
  failed: number
  items: BatchItem[]
}

export interface DiscoverArticle {
  title: string
  url: string
  published: string
  summary?: string
}

export interface DiscoverResult {
  domain: string
  site: string
  feed: string
  source: string
  since: string
  count: number
  articles: DiscoverArticle[]
}

export interface SearchHit {
  article_id: string
  url: string
  title: string
  site: string
  word_count: number
  fetched_at: string
  paywalled_preview: boolean
  snippet: string
}

export interface DomainStat {
  domain: string
  tries: number
  wins: number
  fails: number
  best_stage: string
  success_rate: number
  last_win_at: number | null
  stages: Array<{ stage: string; tries: number; wins: number; avg_ms: number }>
}
