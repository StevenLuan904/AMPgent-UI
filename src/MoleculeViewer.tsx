import { useEffect, useRef, useState } from 'react'
import 'molstar/lib/mol-plugin-ui/skin/light.scss'
import type { ViewerArtifact } from './types'

interface Props {
  artifact: ViewerArtifact | null
  compact?: boolean
  autoRotate?: boolean
}

export function MoleculeViewer({ artifact, compact = false, autoRotate = false }: Props) {
  const host = useRef<HTMLDivElement>(null)
  // Mol* is intentionally loaded only when the structure inspector is opened.
  const pluginRef = useRef<any>(null)
  const [pluginReady, setPluginReady] = useState(false)
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')

  useEffect(() => {
    if (!host.current || pluginRef.current) return
    let disposed = false
    Promise.all([
      import('molstar/lib/mol-plugin-ui'),
      import('molstar/lib/mol-plugin-ui/react18'),
      import('molstar/lib/mol-plugin-ui/spec'),
    ]).then(([pluginUi, react18, pluginSpec]) => {
      return pluginUi.createPluginUI({
        target: host.current!,
        render: react18.renderReact18,
        spec: {
          ...pluginSpec.DefaultPluginUISpec(),
          layout: {
            initial: {
              isExpanded: false,
              showControls: false,
              controlsDisplay: 'reactive',
            },
          },
        },
      })
    }).then((plugin) => {
      if (disposed) {
        plugin.dispose()
        return
      }
      pluginRef.current = plugin
      setPluginReady(true)
    })
    return () => {
      disposed = true
      pluginRef.current?.dispose()
      pluginRef.current = null
    }
  }, [])

  useEffect(() => {
    const plugin = pluginRef.current
    if (!plugin || !artifact) return
    let cancelled = false
    const load = async () => {
      setState('loading')
      try {
        await plugin.clear()
        const artifactUrl = artifact.artifact_url.startsWith('/')
          ? new URL(artifact.artifact_url, window.location.origin).toString()
          : artifact.artifact_url
        const data = await plugin.builders.data.download(
          { url: artifactUrl, isBinary: false },
          { state: { isGhost: true } },
        )
        const format = artifact.media_type.includes('cif') ? 'mmcif' : 'pdb'
        const trajectory = await plugin.builders.structure.parseTrajectory(data, format)
        await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default')
        if (autoRotate) {
          plugin.canvas3d?.setProps({
            trackball: { animate: { name: 'spin', params: { speed: 0.0025, axis: [0, 1, 0] } } },
          })
        }
        if (!cancelled) setState('ready')
      } catch (error) {
        console.error('Unable to load structure artifact', error)
        if (!cancelled) setState('error')
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [artifact, autoRotate, pluginReady])

  return (
    <div className={`molstar-shell${compact ? ' mini-molstar-shell' : ''}`}>
      <div ref={host} className="molstar-host" />
      {!artifact && <div className="viewer-empty">此轮次尚无可读取的结构文件</div>}
      {artifact && state === 'loading' && <div className="viewer-state">正在验证并载入结构…</div>}
      {artifact && state === 'error' && <div className="viewer-state viewer-error">结构载入失败</div>}
      {artifact && state === 'ready' && !compact && (
        <div className="viewer-tag" title="Mol*是用于交互审视生物大分子三维结构的可视化工具。">
          <span className="live-dot" /> Mol* · {artifact.lane === 'native' ? '原位' : '错误口袋对照'} · 随机种子 {artifact.seed}
        </div>
      )}
    </div>
  )
}
