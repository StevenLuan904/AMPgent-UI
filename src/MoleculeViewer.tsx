import { useEffect, useRef, useState } from 'react'
import 'molstar/lib/mol-plugin-ui/skin/light.scss'
import { MolScriptBuilder as MS } from 'molstar/lib/mol-script/language/builder'
import { PluginConfig } from 'molstar/lib/mol-plugin/config'
import { Color } from 'molstar/lib/mol-util/color/index'
import type { ViewerArtifact } from './types'

interface Props {
  artifact: ViewerArtifact | null
  compact?: boolean
  autoRotate?: boolean
  representation?: 'cartoon' | 'atomic' | 'surface'
  colorTheme?: 'baker-spectrum' | 'chain-id' | 'hydrophobicity' | 'element-symbol'
  pocketResidues?: number[]
  receptorChain?: string
  peptideChain?: string
  interactive?: boolean
}

const representationPresets = {
  cartoon: 'polymer-cartoon',
  atomic: 'atomic-detail',
  surface: 'molecular-surface',
} as const

const pocketSpectrum = {
  kind: 'interpolate' as const,
  colors: [0xa8e0ee, 0xc5e1a3, 0xffe38b].map(Color),
}

const peptideSpectrum = {
  kind: 'interpolate' as const,
  colors: [0xb0a3d1, 0x8bd0d5].map(Color),
}

export function MoleculeViewer({
  artifact,
  compact = false,
  autoRotate = false,
  representation = 'cartoon',
  colorTheme = 'baker-spectrum',
  pocketResidues = [],
  receptorChain = 'A',
  peptideChain = 'B',
  interactive = true,
}: Props) {
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
      const defaultSpec = pluginSpec.DefaultPluginUISpec()
      return pluginUi.createPluginUI({
        target,
        render: react18.renderReact18,
        spec: {
          ...defaultSpec,
          config: [
            ...(defaultSpec.config ?? []),
            [PluginConfig.General.PixelScale, compact ? 1.5 : 2],
            [PluginConfig.General.ResolutionMode, 'scaled'],
          ],
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
          ambientIntensity: 0.72,
          light: [
            { inclination: 155, azimuth: 25, color: Color(0xffffff), intensity: 0.52 },
            { inclination: 35, azimuth: 215, color: Color(0xdde8f6), intensity: 0.2 },
          ],
          exposure: 1,
          celSteps: 0,
        },
        multiSample: { mode: 'on', sampleLevel: compact ? 1 : 3, reduceFlicker: true, reuseOcclusion: true },
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
        const model = await plugin.builders.structure.createModel(trajectory)
        const structure = await plugin.builders.structure.createStructure(model)
        const peptideExpression = MS.struct.generator.atomGroups({
          'chain-test': MS.core.rel.eq([
            MS.struct.atomProperty.macromolecular.auth_asym_id(),
            peptideChain,
          ]),
        })
        const pocketExpression = MS.struct.modifier.exceptBy({
          0: MS.struct.modifier.includeSurroundings({ 0: peptideExpression, radius: 8, 'as-whole-residues': true }),
          by: peptideExpression,
        })
        const pocket = await plugin.builders.structure.tryCreateComponentFromExpression(structure, pocketExpression, 'pocket', { label: '口袋残基' })
        const peptide = await plugin.builders.structure.tryCreateComponentFromExpression(structure, peptideExpression, 'peptide', { label: '候选短肽' })
        const resolvedColorTheme = colorTheme === 'baker-spectrum' ? 'sequence-id' : colorTheme
        const pocketColorParams = colorTheme === 'baker-spectrum' ? { list: pocketSpectrum } : undefined
        const peptideColorParams = colorTheme === 'baker-spectrum' ? { list: peptideSpectrum } : undefined
        const quality = compact ? 'high' : 'higher'
        if (pocket) {
          await plugin.builders.structure.representation.addRepresentation(pocket, {
            type: 'molecular-surface',
            typeParams: { alpha: 0.3, quality, resolution: compact ? 0.65 : 0.35, probeRadius: 1.4 },
            color: resolvedColorTheme,
            colorParams: pocketColorParams,
          })
          await plugin.builders.structure.representation.addRepresentation(pocket, {
            type: 'cartoon',
            typeParams: { alpha: 0.3, quality },
            color: resolvedColorTheme,
            colorParams: pocketColorParams,
          })
        }
        if (peptide) {
          const peptideRepresentation = representation === 'atomic'
            ? 'ball-and-stick'
            : representation === 'surface' ? 'molecular-surface' : 'cartoon'
          await plugin.builders.structure.representation.addRepresentation(peptide, {
            type: peptideRepresentation,
            typeParams: { alpha: 1, quality, sizeFactor: representation === 'atomic' ? 0.34 : undefined },
            color: resolvedColorTheme,
            colorParams: peptideColorParams,
          })
          if (representation === 'cartoon') {
            await plugin.builders.structure.representation.addRepresentation(peptide, {
              type: 'ball-and-stick',
              typeParams: { alpha: 0.62, quality, sizeFactor: 0.18 },
              color: resolvedColorTheme,
              colorParams: peptideColorParams,
            })
          }
        }
        if (!pocket || !peptide) {
          await plugin.builders.structure.representation.applyPreset(structure, representationPresets[representation], {
            theme: { globalName: resolvedColorTheme },
            quality,
          })
        } else {
          window.setTimeout(() => {
            plugin.managers.camera.orientAxes([pocket.cell?.obj?.data, peptide.cell?.obj?.data].filter(Boolean), 0)
            plugin.managers.camera.focusSpheres(
              [pocket, peptide],
              (component: any) => component.cell?.obj?.data?.boundary?.sphere,
              { minRadius: 7, extraRadius: compact ? 2 : 3, durationMs: 0 },
            )
            window.setTimeout(() => {
              const snapshot = plugin.canvas3d?.camera.getSnapshot()
              if (!snapshot) return
              const direction = [
                snapshot.position[0] - snapshot.target[0],
                snapshot.position[1] - snapshot.target[1],
                snapshot.position[2] - snapshot.target[2],
              ]
              const rolledUp = [
                direction[1] * snapshot.up[2] - direction[2] * snapshot.up[1],
                direction[2] * snapshot.up[0] - direction[0] * snapshot.up[2],
                direction[0] * snapshot.up[1] - direction[1] * snapshot.up[0],
              ]
              const length = Math.hypot(...rolledUp) || 1
              const zoomScale = compact ? 0.76 : 0.44
              plugin.managers.camera.setSnapshot({
                up: rolledUp.map((value) => value / length),
                position: snapshot.target.map((value: number, index: number) => value + direction[index] * zoomScale),
                radius: snapshot.radius * zoomScale,
              }, 0)
            }, 60)
          }, 80)
        }
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
  }, [artifact?.artifact_url, artifact?.media_type, colorTheme, peptideChain, pluginReady, pocketResidues.join(','), receptorChain, representation])

  useEffect(() => {
    const plugin = pluginRef.current
    if (!plugin) return
    plugin.canvas3d?.setProps({
      trackball: {
        animate: autoRotate ? { name: 'spin', params: { speed: 0.00055, axis: [0, 1, 0] } } : { name: 'off', params: {} },
        ...(interactive ? {} : { noScroll: true, rotateSpeed: 0, zoomSpeed: 0, panSpeed: 0 }),
      },
    })
  }, [autoRotate, interactive, pluginReady])

  return (
    <div className={`molstar-shell${compact ? ' mini-molstar-shell' : ''}`}>
      <div ref={host} className="molstar-host" />
      {!artifact && <div className="viewer-empty">此轮次尚无可读取的结构文件</div>}
      {artifact && state === 'loading' && <div className="viewer-state">正在验证并载入结构…</div>}
      {artifact && state === 'error' && <div className="viewer-state viewer-error">结构载入失败</div>}
      {artifact && state === 'ready' && !compact && (
        <div className="viewer-tag" title="口袋采用三色透明表面，短肽采用双色序列着色。">
          <span className="live-dot" /> Mol* · {artifact.lane === 'native' ? '原位界面' : '对照界面'}
        </div>
      )}
    </div>
  )
}
