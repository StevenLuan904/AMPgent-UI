import type { AnalysisSnapshot } from './analysisDataContracts'

export interface SnapshotLoadAttempt {
  source: 'analytics_api' | 'frozen_release_snapshot'
  url: string
  error: string
}

export class AnalysisSnapshotLoadError extends Error {
  readonly attempts: SnapshotLoadAttempt[]

  constructor(attempts: SnapshotLoadAttempt[]) {
    super(`Unable to load a valid analysis snapshot (${attempts.map((item) => item.source).join(', ')}).`)
    this.name = 'AnalysisSnapshotLoadError'
    this.attempts = attempts
  }
}

export interface SnapshotValidationIssue {
  path: string
  message: string
}

export function validateAnalysisSnapshot(value: unknown): SnapshotValidationIssue[] {
  const issues: SnapshotValidationIssue[] = []
  if (!value || typeof value !== 'object') return [{ path: '', message: 'Snapshot must be an object.' }]
  const snapshot = value as Partial<AnalysisSnapshot>
  if (snapshot.schemaVersion !== 'ampgent-analysis-snapshot.1') issues.push({ path: 'schemaVersion', message: 'Unsupported snapshot schema.' })
  if (!snapshot.snapshotId) issues.push({ path: 'snapshotId', message: 'snapshotId is required.' })
  if (!/^[a-f0-9]{64}$/.test(snapshot.snapshotSha256 ?? '')) issues.push({ path: 'snapshotSha256', message: 'snapshotSha256 must be a 64-character lowercase hex digest.' })
  if (!snapshot.run?.id) issues.push({ path: 'run.id', message: 'run.id is required.' })
  if (!Array.isArray(snapshot.occurrences)) issues.push({ path: 'occurrences', message: 'occurrences must be an array.' })
  if (!Array.isArray(snapshot.candidates)) issues.push({ path: 'candidates', message: 'candidates must be an array.' })
  if (!snapshot.metricMethods || typeof snapshot.metricMethods !== 'object') issues.push({ path: 'metricMethods', message: 'metricMethods is required.' })
  if (!snapshot.coverage || snapshot.coverage.observed < 0 || snapshot.coverage.expected < 0 || snapshot.coverage.missing < 0) {
    issues.push({ path: 'coverage', message: 'coverage values must be non-negative.' })
  }
  if (snapshot.summary && Array.isArray(snapshot.occurrences) && snapshot.summary.rawOccurrences !== snapshot.occurrences.length) {
    issues.push({ path: 'summary.rawOccurrences', message: 'raw occurrence summary does not match row count.' })
  }
  if (snapshot.summary && Array.isArray(snapshot.candidates) && snapshot.summary.uniqueCandidates !== snapshot.candidates.length) {
    issues.push({ path: 'summary.uniqueCandidates', message: 'unique candidate summary does not match row count.' })
  }
  return issues
}

const DIGEST_PATTERN = /"snapshotSha256":"[a-f0-9]{64}"/
const DIGEST_PLACEHOLDER = `"snapshotSha256":"${'0'.repeat(64)}"`
let verifiedSessionSnapshot: AnalysisSnapshot | null = null

export async function computeAnalysisSnapshotTextDigest(rawText: string): Promise<string> {
  const trimmed = rawText.trim()
  if (!DIGEST_PATTERN.test(trimmed)) throw new Error('snapshotSha256 field is missing or malformed.')
  const digestInput = trimmed.replace(DIGEST_PATTERN, DIGEST_PLACEHOLDER)
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(digestInput))
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function verifyAnalysisSnapshotDigest(snapshot: AnalysisSnapshot, rawText: string): Promise<boolean> {
  return (await computeAnalysisSnapshotTextDigest(rawText)) === snapshot.snapshotSha256
}

async function fetchSnapshot(
  fetchImpl: typeof fetch,
  url: string,
  source: SnapshotLoadAttempt['source'],
  verifyDigest: boolean,
): Promise<AnalysisSnapshot> {
  const response = await fetchImpl(url, { headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const rawText = await response.text()
  let payload: unknown
  try {
    payload = JSON.parse(rawText)
  } catch {
    throw new Error('Response is not valid JSON.')
  }
  const issues = validateAnalysisSnapshot(payload)
  if (issues.length) throw new Error(issues.map((issue) => `${issue.path}: ${issue.message}`).join('; '))
  const snapshot = payload as AnalysisSnapshot
  if (verifyDigest && !(await verifyAnalysisSnapshotDigest(snapshot, rawText))) throw new Error('snapshotSha256: digest mismatch')
  const verified = { ...snapshot, source }
  if (source === 'frozen_release_snapshot') verifiedSessionSnapshot = verified
  return verified
}

export async function loadAnalysisSnapshot(options: {
  runId?: string
  apiBase?: string
  snapshotUrl?: string
  fetchImpl?: typeof fetch
  verifyDigest?: boolean
} = {}): Promise<AnalysisSnapshot> {
  const fetchImpl = options.fetchImpl ?? fetch
  const verifyDigest = options.verifyDigest ?? true
  const attempts: SnapshotLoadAttempt[] = []
  if (options.runId) {
    const url = `${options.apiBase ?? ''}/v1/analytics/runs/${encodeURIComponent(options.runId)}`
    try {
      return await fetchSnapshot(fetchImpl, url, 'analytics_api', verifyDigest)
    } catch (error) {
      attempts.push({ source: 'analytics_api', url, error: error instanceof Error ? error.message : String(error) })
    }
  }

  const snapshotUrl = options.snapshotUrl ?? '/data/launch-analysis.snapshot.json'
  try {
    const snapshot = await fetchSnapshot(fetchImpl, snapshotUrl, 'frozen_release_snapshot', verifyDigest)
    if (attempts.length) {
      snapshot.warnings = [...snapshot.warnings, `Live analytics unavailable; loaded frozen snapshot (${attempts[0].error}).`]
    }
    return snapshot
  } catch (error) {
    attempts.push({ source: 'frozen_release_snapshot', url: snapshotUrl, error: error instanceof Error ? error.message : String(error) })
    if (!options.fetchImpl && verifiedSessionSnapshot) {
      return {
        ...verifiedSessionSnapshot,
        warnings: [...verifiedSessionSnapshot.warnings, '网络不可用；使用本次会话中已校验的冻结快照。'],
      }
    }
    throw new AnalysisSnapshotLoadError(attempts)
  }
}
