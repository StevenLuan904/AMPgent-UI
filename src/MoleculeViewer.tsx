import { useEffect, useRef, useState } from 'react'
import 'molstar/lib/mol-plugin-ui/skin/light.scss'
import { Color } from 'molstar/lib/mol-util/color/index'
import type { ViewerArtifact } from './types'

interface Props {
  artifact: ViewerArtifact | null
  compact?: boolean
  autoRotate?: boolean
  representation?: 'cartoon' | 'atomic' | 'surface'
  colorTheme?: 'baker-spectrum' | 'chain-id' | 'hydrophobicity' | 'element-symbol'
}

const representationPresets = {
  cartoon: 'polymer-cartoon',
  atomic: 'atomic-detail',
  surface: 'molecular-surface',
} as const

const bakerSpectrum = {
  kind: 'interpolate' as const,
  colors: [0xb0a3d1, 0x8bd0d5, 0xa8e0ee, 0xc5e1a3, 0xffe38b, 0xf5a37d, 0xe88db2].map(Color),
}

export function MoleculeViewer({ artifact, compact = false, autoRotate = false, representation = 'cartoon', colorTheme = 'baker-spectrum' }: Props) {
  const host = useRef<HTMLDivElement>(null)
  // Mol* is intentionally loaded only when the structure inspector is opened.
  const pluginRef = useRef<any>(null)
  const [pluginReady, setPluginReady] = useState(false)
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')

  useEffect(() => {
    if (!artifact) {
      setPluginReady(false)
      setState('idle')
      return
    }
    if (!host.current || pluginRef.current) return
    const target = host.current
    let disposed = false
    Promise.all([
      import('molstar/lib/mol-plugin-ui'),
      import('molstar/lib/mol-plugin-ui/react18'),
      import('molstar/lib/mol-plugin-ui/spec'),
    ]).then(([pluginUi, react18, pluginSpec]) => {
      return pluginUi.createPluginUI({
        target,
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
      plugin.canvas3d?.setProps({
        camera: { mode: 'orthographic', helper: { axes: { name: 'off', params: {} } } },
        cameraFog: { name: 'off', params: {} },
        renderer: {
          backgroundColor: Color(0xffffff),
          ambientColor: Color(0xffffff),
          ambientIntensity: 1,
          light: [],
          exposure: 1,
          celSteps: 0,
        },
        postprocessing: {
          enabled: true,
          occlusion: { name: 'off', params: {} },
          shadow: { name: 'off', params: {} },
          outline: { name: 'on', params: { scale: 1, threshold: 0.1, color: Color(0x667085), includeTransparent: true } },
          dof: { name: 'off', params: {} },
          antialiasing: { name: 'smaa', params: { edgeThreshold: 0.1, maxSearchSteps: 32 } },
          sharpening: { name: 'off', params: {} },
          bloom: { name: 'off', params: {} },
        },
        illumination: { enabled: false, shadowEnable: false },
      })
      setPluginReady(true)
    })
    return () => {
      disposed = true
      pluginRef.current?.dispose()
      pluginRef.current = null
    }
  }, [!!artifact])

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
        const resolvedColorTheme = colorTheme === 'baker-spectrum' ? 'sequence-id' : colorTheme
        await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default', {
          representationPreset: representationPresets[representation],
          representationPresetParams: {
            theme: {
              globalName: resolvedColorTheme,
              globalColorParams: colorTheme === 'baker-spectrum' ? { list: bakerSpectrum } : undefined,
            },
            quality: compact ? 'medium' : 'high',
            ignoreLight: true,
          },
        })
        if (!cancelled) setState('ready')
      } catch (error) {
        if (!cancelled) {
          console.error('Unable to load structure artifact', error)
          setState('error')
        }
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [artifact?.artifact_url, artifact?.media_type, colorTheme, pluginReady, representation])

  useEffect(() => {
    const plugin = pluginRef.current
    if (!plugin) return
    plugin.canvas3d?.setProps({
      trackball: { animate: autoRotate ? { name: 'spin', params: { speed: 0.0012, axis: [0, 1, 0] } } : { name: 'off', params: {} } },
    })
  }, [autoRotate, pluginReady])

  return (
    <div className={`molstar-shell${compact ? ' mini-molstar-shell' : ''}`}>
      <div ref={host} className="molstar-host" />
      {!artifact && <div className="viewer-empty">此轮次尚无可读取的结构文件</div>}
      {artifact && state === 'loading' && <div className="viewer-state">正在验证并载入结构…</div>}
      {artifact && state === 'error' && <div className="viewer-state viewer-error">结构载入失败</div>}
      {artifact && state === 'ready' && !compact && (
        <div className="viewer-tag" title="Baker 风格按残基序号使用七色渐变，并关闭高光、雾化和阴影。">
          <span className="live-dot" /> Mol* · Baker 序列谱 · {artifact.lane === 'native' ? '原位' : '错误口袋对照'} · 随机种子 {artifact.seed}
        </div>
      )}
    </div>
  )
}
