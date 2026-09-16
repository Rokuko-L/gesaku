import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import fcose from 'cytoscape-fcose'

cytoscape.use(fcose)

const KIND_COLOR = {
  character: '#ff6a3d',
  location: '#e1bfb6',
  faction: '#a98a81',
}

/* group colors cycle through the console palette (ember reserved for the
   protagonist-tier highlight, cyan/olive/rose as cluster hues) */
const GROUP_COLORS = ['#58d7eb', '#cbc7b5', '#ffb4ab', '#ff6a3d', '#a98a81', '#e1bfb6', '#c9a89e']

const EDGE_STYLE = {
  ally: { 'line-color': '#cbc7b5', 'line-style': 'solid' },
  rival: { 'line-color': '#ffb4ab', 'line-style': 'solid' },
  family: { 'line-color': '#e1bfb6', 'line-style': 'solid' },
  mentor: { 'line-color': '#58d7eb', 'line-style': 'solid' },
  secret: { 'line-color': '#ff6a3d', 'line-style': 'dashed' },
  serves: { 'line-color': '#a98a81', 'line-style': 'dashed' },
  unknown: { 'line-color': '#59413a', 'line-style': 'solid' },
}

/**
 * Force-directed entity graph (cytoscape). Two data dialects:
 *  - heuristic (co-mention graph): nodes {id,label,kind,status}, edges {from,to,label}
 *  - llm-arranged: nodes gain {group, importance, desc=one-line summary},
 *    edges gain {kind} — nodes are sized by importance, colored by group,
 *    and edge color/style encodes the relationship kind.
 * Hover lights the neighborhood; tap fires onSelect(nodeData).
 */
export default function EntityGraph({ nodes, edges, arranged = false, onSelect, className = 'h-[480px]' }) {
  const ref = useRef(null)
  const cyRef = useRef(null)

  useEffect(() => {
    // group → color assignment is stable per render pass
    const groupColor = {}
    let gi = 0
    for (const n of nodes) {
      if (n.group && !(n.group in groupColor)) {
        groupColor[n.group] = GROUP_COLORS[gi++ % GROUP_COLORS.length]
      }
    }

    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...nodes.map((n) => ({
          data: {
            id: n.id, label: n.label, kind: n.kind, status: n.status,
            group: n.group ?? null,
            size: arranged ? 10 + (n.importance ?? 5) * 1.6 : 20,
            color: arranged
              ? groupColor[n.group] ?? KIND_COLOR[n.kind] ?? '#59413a'
              : KIND_COLOR[n.kind] ?? '#59413a',
          },
        })),
        ...edges.map((e, i) => ({
          data: {
            id: `e${i}`, source: e.from, target: e.to, label: e.label,
            kind: e.kind ?? 'unknown',
          },
        })),
      ],
      layout: {
        name: 'fcose',
        animate: true,
        padding: 50,
        nodeSeparation: arranged ? 220 : 240,
        idealEdgeLength: arranged ? 170 : 220,
        nodeRepulsion: arranged ? 12000 : 24000,
        gravity: 0.25,
        packComponents: true,
      },
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'background-color': '#261815',
            'border-color': 'data(color)',
            'border-width': (el) => 2 + (el.data('size') - 10) / 6,
            shape: 'rectangle',
            color: '#efd2ca',
            'font-family': '"JetBrains Mono", ui-monospace, monospace',
            'font-size': (el) => Math.min(11, 8 + (el.data('size') - 10) / 4),
            'text-valign': 'bottom',
            'text-margin-y': 5,
            'text-wrap': 'wrap',
            'text-max-width': 110,
            'text-background-color': '#1d100d',
            'text-background-opacity': 1,
            'text-background-padding': 2,
            width: 'data(size)',
            height: 'data(size)',
          },
        },
        {
          selector: 'edge',
          style: {
            width: 1.5,
            'line-color': '#59413a',
            'curve-style': 'bezier',
            opacity: 1,
          },
        },
        {
          selector: 'edge.lit',
          style: {
            label: 'data(label)',
            width: 2.5,
            color: '#ff6a3d',
            'font-size': 8,
            'font-family': '"JetBrains Mono", ui-monospace, monospace',
            'text-background-color': '#1d100d',
            'text-background-opacity': 1,
            'text-background-padding': 2,
            'text-rotation': 'autorotate',
          },
        },
        {
          selector: 'node.dead',
          style: { 'border-style': 'dashed', color: '#ffb4ab' },
        },
        {
          selector: '.dimmed',
          style: { opacity: 0.12 },
        },
      ],
      wheelSensitivity: 0.2,
    })

    // edge kinds color/style the relationship (llm-arranged graphs)
    cy.edges().forEach((e) => {
      const style = EDGE_STYLE[e.data('kind')] ?? EDGE_STYLE.unknown
      e.style(style)
    })

    // dead/doomed characters get dashed rings via status text
    cy.nodes().forEach((n) => {
      const s = (n.data('status') ?? '').toLowerCase()
      if (s.includes('dead') || s.includes('unknown')) n.addClass('dead')
    })

    const focus = (evt) => {
      const node = evt.target
      const nbhd = node.closedNeighborhood()
      cy.elements().addClass('dimmed')
      nbhd.removeClass('dimmed')
      nbhd.edges().addClass('lit')
    }
    const unfocus = () => {
      cy.elements().removeClass('dimmed')
      cy.edges().removeClass('lit')
    }
    cy.on('layoutstop', () => cy.fit(undefined, 60))
    cy.on('mouseover', 'node', focus)
    cy.on('mouseout', 'node', unfocus)
    if (onSelect) {
      cy.on('tap', 'node', (evt) => {
        const d = evt.target.json().data
        onSelect({
          id: d.id, label: d.label, kind: d.kind, status: d.status,
          group: d.group, importance: d.importance,
        })
      })
    }

    cyRef.current = cy
    return () => cy.destroy()
  }, [nodes, edges, arranged])

  return (
    <div className="relative h-full w-full overflow-hidden border border-line bg-ink-950">
      <div ref={ref} className={`${className} w-full`} />
      {arranged && (
        <div className="pointer-events-none absolute bottom-3 left-4 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-fog-500">
          {Object.entries(EDGE_STYLE).slice(0, 6).map(([kind, s]) => (
            <span key={kind} className="flex items-center gap-1">
              <span
                className="inline-block h-0.5 w-4"
                style={{ background: s['line-color'], borderTop: s['line-style'] === 'dashed' ? '2px dashed' : 'none', height: 2 }}
              />
              {kind}
            </span>
          ))}
        </div>
      )}
      <p className="pointer-events-none absolute bottom-3 right-4 font-mono text-[10px] text-fog-500">
        hover to trace · click to inspect · scroll to zoom
      </p>
    </div>
  )
}
