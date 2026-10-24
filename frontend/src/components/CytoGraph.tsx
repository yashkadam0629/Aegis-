import { useEffect, useRef } from 'react'
import cytoscape, { Core, ElementDefinition } from 'cytoscape'
import type { GraphEdge, GraphNode } from '../types'
import { BAND_COLOR } from '../utils/format'

export type ColorMode = 'type' | 'community' | 'risk'
const TYPE_COLOR: Record<string, string> = { individual: '#8a93a6', business: '#2457c5', merchant: '#0e7490', payment_aggregator: '#7a5aa6', beneficiary: '#a16207', unknown: '#b8bfcc' }
const COMM = ['#2457c5', '#c2570c', '#3f7d58', '#7a5aa6', '#a16207', '#0e7490', '#b42318', '#657089', '#4b9d8f', '#9b4f96']

function riskColor(r: number) {
  return r >= 0.8 ? BAND_COLOR.Critical : r >= 0.6 ? BAND_COLOR.High : r >= 0.4 ? BAND_COLOR.Medium : r >= 0.2 ? BAND_COLOR.Low : '#b8bfcc'
}

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  centerId?: string
  colorMode: ColorMode
  highlightNodes?: Set<string>
  highlightEdges?: Set<string> // "src>dst"
  pathNodes?: Set<string>
  onNodeSelect?: (id: string | null) => void
  onEdgeSelect?: (e: GraphEdge | null) => void
  focusId?: string | null
  height?: number
}

export function edgeKey(s: string, t: string) {
  return `${s}>${t}`
}

export function CytoGraph({ nodes, edges, centerId, colorMode, highlightNodes, highlightEdges, pathNodes, onNodeSelect, onEdgeSelect, focusId, height = 560 }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)
  const handlers = useRef({ onNodeSelect, onEdgeSelect })
  handlers.current = { onNodeSelect, onEdgeSelect }
  const edgeMap = useRef<Map<string, GraphEdge>>(new Map())

  // (re)build elements when the data changes
  useEffect(() => {
    if (!host.current) return
    if (!cy.current) {
      try {
        cy.current = cytoscape({
          container: host.current,
          wheelSensitivity: 0.25,
          minZoom: 0.1,
          maxZoom: 3,
          style: [
            { selector: 'node', style: { width: 'data(size)', height: 'data(size)', 'background-color': 'data(color)', 'border-width': 'data(bw)', 'border-color': 'data(bc)', label: 'data(label)', 'font-size': 8, color: '#363f53', 'text-valign': 'bottom', 'text-margin-y': 3, 'text-opacity': 0, 'min-zoomed-font-size': 7 } },
            { selector: 'node.hl', style: { 'border-width': 3, 'border-color': '#b42318', 'text-opacity': 1 } },
            { selector: 'node.path', style: { 'border-width': 3, 'border-color': '#1d46a0', 'text-opacity': 1 } },
            { selector: 'node.center', style: { 'border-width': 3, 'border-color': '#151a27', 'text-opacity': 1 } },
            { selector: 'node:selected', style: { 'border-width': 4, 'border-color': '#2f6bdc', 'text-opacity': 1 } },
            { selector: 'edge', style: { width: 'data(w)', 'line-color': '#b8bfcc', 'target-arrow-color': '#8a93a6', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8, 'curve-style': 'bezier', opacity: 0.8 } },
            { selector: 'edge.hl', style: { 'line-color': '#b42318', 'target-arrow-color': '#b42318', opacity: 1, width: 3 } },
            { selector: 'edge:selected', style: { 'line-color': '#2f6bdc', 'target-arrow-color': '#2f6bdc', width: 4, opacity: 1 } },
            { selector: '.dim', style: { opacity: 0.18 } },
          ],
        })
      } catch {
        return // no canvas in test environment
      }
      cy.current.on('tap', 'node', (e) => handlers.current.onNodeSelect?.(e.target.id()))
      cy.current.on('tap', 'edge', (e) => handlers.current.onEdgeSelect?.(edgeMap.current.get(e.target.id()) ?? null))
      cy.current.on('tap', (e) => {
        if (e.target === cy.current) {
          handlers.current.onNodeSelect?.(null)
          handlers.current.onEdgeSelect?.(null)
        }
      })
    }
    const c = cy.current
    edgeMap.current = new Map()
    const els: ElementDefinition[] = nodes.map((n) => ({
      data: { id: n.id, label: n.id, size: 14 + Math.min(22, Math.sqrt(n.alerts.length * 40)), color: '#8a93a6', bw: 1, bc: '#ffffff' },
      classes: n.id === centerId ? 'center' : '',
    }))
    const ids = new Set(nodes.map((n) => n.id))
    for (const e of edges) {
      if (!ids.has(e.source) || !ids.has(e.target)) continue
      const id = `e:${e.source}>${e.target}`
      edgeMap.current.set(id, e)
      els.push({ data: { id, source: e.source, target: e.target, w: 1 + Math.min(5, Math.log10(Math.max(e.total, 10)) - 2) } })
    }
    c.batch(() => {
      c.elements().remove()
      c.add(els)
    })
    const layout = c.layout({ name: 'cose', animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 70, padding: 20, randomize: true, numIter: 800 } as cytoscape.LayoutOptions)
    layout.run()
  }, [nodes, edges, centerId])

  // styling overlays
  useEffect(() => {
    const c = cy.current
    if (!c) return
    const byId = new Map(nodes.map((n) => [n.id, n]))
    c.batch(() => {
      c.nodes().forEach((n) => {
        const d = byId.get(n.id())
        if (!d) return
        const color = colorMode === 'type' ? TYPE_COLOR[d.account_type] || TYPE_COLOR.unknown : colorMode === 'community' ? (d.community === null ? '#b8bfcc' : COMM[d.community % COMM.length]) : riskColor(d.max_risk)
        n.data('color', color)
        n.data('bw', d.max_risk >= 0.4 && colorMode !== 'risk' ? 3 : 1)
        n.data('bc', d.max_risk >= 0.4 ? riskColor(d.max_risk) : '#ffffff')
        n.toggleClass('hl', !!highlightNodes?.has(n.id()))
        n.toggleClass('path', !!pathNodes?.has(n.id()))
      })
      c.edges().forEach((e) => { e.toggleClass('hl', !!highlightEdges?.has(edgeKey(e.source().id(), e.target().id()))) })
      const any = (highlightNodes && highlightNodes.size > 0) || (pathNodes && pathNodes.size > 0)
      c.elements().toggleClass('dim', false)
      if (any) {
        c.nodes().forEach((n) => { n.toggleClass('dim', !(highlightNodes?.has(n.id()) || pathNodes?.has(n.id()) || n.hasClass('center'))) })
        c.edges().forEach((e) => { e.toggleClass('dim', !(e.hasClass('hl') || (pathNodes?.has(e.source().id()) && pathNodes?.has(e.target().id())))) })
      }
    })
  }, [nodes, colorMode, highlightNodes, highlightEdges, pathNodes])

  useEffect(() => {
    const c = cy.current
    if (!c || !focusId) return
    const n = c.getElementById(focusId)
    if (n.nonempty()) {
      c.elements().unselect()
      n.select()
      c.animate({ center: { eles: n }, zoom: Math.max(c.zoom(), 1.2) }, { duration: 200 })
    }
  }, [focusId])

  useEffect(() => () => { cy.current?.destroy(); cy.current = null }, [])

  return (
    <div className="relative" style={{ height }}>
      <div ref={host} className="h-full w-full rounded border border-ink-200 bg-white" role="img" aria-label="Interactive account network graph" data-testid="graph-canvas" />
      <div className="absolute right-2 top-2 flex flex-col gap-1">
        <button className="btn !px-2 !py-1" onClick={() => cy.current?.zoom({ level: (cy.current?.zoom() ?? 1) * 1.3, renderedPosition: { x: 200, y: 200 } })} aria-label="Zoom in">+</button>
        <button className="btn !px-2 !py-1" onClick={() => cy.current?.zoom({ level: (cy.current?.zoom() ?? 1) / 1.3, renderedPosition: { x: 200, y: 200 } })} aria-label="Zoom out">−</button>
        <button className="btn !px-2 !py-1" onClick={() => cy.current?.fit(undefined, 30)} aria-label="Fit graph">Fit</button>
      </div>
    </div>
  )
}
