import { describe, expect, it, vi } from 'vitest'
import {
  AnalysisSnapshotLoadError,
  computeAnalysisSnapshotTextDigest,
  loadAnalysisSnapshot,
  validateAnalysisSnapshot,
  verifyAnalysisSnapshotDigest,
} from './snapshotAdapter'
import { createTestSnapshot } from './testSnapshot'

const response = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

describe('snapshot runtime validation', () => {
  it('accepts the complete contract', () => {
    expect(validateAnalysisSnapshot(createTestSnapshot())).toEqual([])
  })

  it('rejects null and scalar payloads', () => {
    expect(validateAnalysisSnapshot(null)[0].message).toContain('object')
    expect(validateAnalysisSnapshot('bad')[0].message).toContain('object')
  })

  it('rejects an unsupported schema version', () => {
    const snapshot = { ...createTestSnapshot(), schemaVersion: 'future' }
    expect(validateAnalysisSnapshot(snapshot).some((issue) => issue.path === 'schemaVersion')).toBe(true)
  })

  it('rejects a malformed digest', () => {
    const snapshot = { ...createTestSnapshot(), snapshotSha256: 'not-a-digest' }
    expect(validateAnalysisSnapshot(snapshot).some((issue) => issue.path === 'snapshotSha256')).toBe(true)
  })

  it('rejects inconsistent occurrence counts', () => {
    const snapshot = createTestSnapshot()
    snapshot.summary.rawOccurrences = 999
    expect(validateAnalysisSnapshot(snapshot).some((issue) => issue.path === 'summary.rawOccurrences')).toBe(true)
  })

  it('rejects inconsistent candidate counts', () => {
    const snapshot = createTestSnapshot()
    snapshot.summary.uniqueCandidates = 999
    expect(validateAnalysisSnapshot(snapshot).some((issue) => issue.path === 'summary.uniqueCandidates')).toBe(true)
  })

  it('rejects a candidate that is not explicitly display eligible', () => {
    const snapshot = createTestSnapshot()
    const candidate = snapshot.candidates[0] as unknown as { displayEligible: boolean }
    candidate.displayEligible = false
    expect(validateAnalysisSnapshot(snapshot).some((issue) => issue.path === 'candidates')).toBe(true)
  })

  it('rejects inconsistent display population denominators', () => {
    const snapshot = createTestSnapshot()
    snapshot.displayPopulation.candidateRecordCount = 7
    expect(validateAnalysisSnapshot(snapshot).some((issue) => issue.path === 'displayPopulation')).toBe(true)
  })

  it('rejects negative coverage', () => {
    const snapshot = createTestSnapshot()
    snapshot.coverage.missing = -1
    expect(validateAnalysisSnapshot(snapshot).some((issue) => issue.path === 'coverage')).toBe(true)
  })

  it('detects digest tampering', async () => {
    const snapshot = createTestSnapshot()
    snapshot.snapshotSha256 = '0'.repeat(64)
    const unsigned = JSON.stringify(snapshot)
    snapshot.snapshotSha256 = await computeAnalysisSnapshotTextDigest(unsigned)
    const sealed = JSON.stringify(snapshot)
    expect(await verifyAnalysisSnapshotDigest(snapshot, sealed)).toBe(true)
    const tampered = sealed.replace('AAAA', 'TAMPERED')
    expect(await verifyAnalysisSnapshotDigest(snapshot, tampered)).toBe(false)
  })
})

describe('live-to-frozen snapshot adapter', () => {
  it('prefers a valid live analytics response', async () => {
    const fetchImpl = vi.fn(async () => response(createTestSnapshot())) as unknown as typeof fetch
    const result = await loadAnalysisSnapshot({ runId: 'run-test', fetchImpl, verifyDigest: false })
    expect(result.source).toBe('analytics_api')
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('falls back on live 404 and exposes the fallback reason', async () => {
    const fetchImpl = vi.fn(async (url: string | URL | Request) =>
      String(url).includes('/v1/analytics/') ? response({}, 404) : response(createTestSnapshot()),
    ) as unknown as typeof fetch
    const result = await loadAnalysisSnapshot({ runId: 'run-test', fetchImpl, verifyDigest: false })
    expect(result.source).toBe('frozen_release_snapshot')
    expect(result.warnings.at(-1)).toContain('HTTP 404')
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('falls back when live JSON violates the contract', async () => {
    const fetchImpl = vi.fn(async (url: string | URL | Request) =>
      String(url).includes('/v1/analytics/') ? response({ wrong: true }) : response(createTestSnapshot()),
    ) as unknown as typeof fetch
    const result = await loadAnalysisSnapshot({ runId: 'run-test', fetchImpl, verifyDigest: false })
    expect(result.source).toBe('frozen_release_snapshot')
    expect(result.warnings.at(-1)).toContain('schemaVersion')
  })

  it('loads frozen data directly when no run id is supplied', async () => {
    const fetchImpl = vi.fn(async () => response(createTestSnapshot())) as unknown as typeof fetch
    const result = await loadAnalysisSnapshot({ snapshotUrl: '/custom.json', fetchImpl, verifyDigest: false })
    expect(result.source).toBe('frozen_release_snapshot')
    expect(fetchImpl).toHaveBeenCalledWith('/custom.json', expect.anything())
  })

  it('reports both failed attempts without silently substituting fixtures', async () => {
    const fetchImpl = vi.fn(async () => response({}, 503)) as unknown as typeof fetch
    await expect(loadAnalysisSnapshot({ runId: 'run-test', fetchImpl, verifyDigest: false })).rejects.toMatchObject({
      name: 'AnalysisSnapshotLoadError',
      attempts: [
        { source: 'analytics_api', url: '/v1/analytics/runs/run-test', error: 'HTTP 503' },
        { source: 'frozen_release_snapshot', url: '/data/launch-analysis.snapshot.json', error: 'HTTP 503' },
      ],
    } satisfies Partial<AnalysisSnapshotLoadError>)
  })

  it('preserves caller snapshot warnings when appending fallback context', async () => {
    const frozen = createTestSnapshot()
    const fetchImpl = vi.fn(async (url: string | URL | Request) =>
      String(url).includes('/v1/analytics/') ? response({}, 500) : response(frozen),
    ) as unknown as typeof fetch
    const result = await loadAnalysisSnapshot({ runId: 'run-test', fetchImpl, verifyDigest: false })
    expect(result.warnings[0]).toBe('Test warning.')
    expect(frozen.warnings).toEqual(['Test warning.'])
  })
})
