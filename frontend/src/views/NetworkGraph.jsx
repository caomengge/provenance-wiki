import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import * as d3 from 'd3'
import api from '../api/client'
import DocPreviewPanel from '../components/DocPreviewPanel'

const NODE_COLORS = {
  document:    '#1a2332',
  person:      '#2563eb',
  object:      '#d97706',
  institution: '#16a34a',
  place:       '#0891b2',
  unknown:     '#6b7280',
}

const NODE_RADIUS = {
  document:    8,
  person:      10,
  object:      9,
  institution: 11,
  place:       9,
  unknown:     8,
}

// Entity types the network can show. People are the default view; the rest
// are opt-in. Documents are not nodes — the graph is entity relationships only.
const ALL_TYPES = ['person', 'object', 'institution', 'place', 'unknown']
const DEFAULT_TYPES = ['person']

export default function NetworkGraph() {
  const svgRef      = useRef(null)
  const graphApiRef = useRef(null)   // holds { applyVisualState } from the last D3 render

  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(true)
  const [selected, setSelected] = useState(null)
  const [searchParams]          = useSearchParams()
  const navigate                = useNavigate()

  // Search & filter state
  const [searchQuery, setSearchQuery] = useState('')
  const [activeTypes, setActiveTypes] = useState(new Set(DEFAULT_TYPES))

  // Entity document list state
  const [docsExpanded, setDocsExpanded] = useState(false)
  const [entityDocs,   setEntityDocs]   = useState([])
  const [docsLoading,  setDocsLoading]  = useState(false)

  // Document preview panel
  const [previewDocId, setPreviewDocId] = useState(null)

  // Fetches the graph for the currently active entity types. Toggling a type
  // re-runs this (the backend only returns the requested types, keeping the
  // payload small) — so type filtering is a refetch, not a client-side hide.
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = { types: [...activeTypes].join(',') }
      const docId = searchParams.get('doc_id')
      if (docId) params.doc_id = docId
      const res = await api.getNetwork(params)
      setData(res)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [searchParams, activeTypes])

  useEffect(() => { load() }, [load])

  // Re-render D3 graph whenever data changes; store the returned API ref
  useEffect(() => {
    if (!data || !svgRef.current) return
    graphApiRef.current = renderGraph(data, svgRef.current, setSelected)
  }, [data])

  // Re-apply the search highlight after a render or when the query changes
  useEffect(() => {
    graphApiRef.current?.applyVisualState(searchQuery)
  }, [searchQuery, data])

  // Reset document list whenever a different node is selected
  useEffect(() => {
    setDocsExpanded(false)
    setEntityDocs([])
  }, [selected?.id])

  const toggleDocs = async () => {
    if (docsExpanded) { setDocsExpanded(false); return }
    setDocsExpanded(true)
    if (entityDocs.length > 0) return
    setDocsLoading(true)
    try {
      const entity = await api.getEntity(selected.db_id)
      setEntityDocs(entity.documents || [])
    } catch (err) {
      console.error(err)
    } finally {
      setDocsLoading(false)
    }
  }

  const toggleType = (type) => {
    setActiveTypes(prev => {
      const next = new Set(prev)
      if (next.has(type)) {
        if (next.size === 1) return prev  // always keep at least one type visible
        next.delete(type)
      } else {
        next.add(type)
      }
      return next
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <div className="page-header" style={{ flexShrink: 0 }}>
        {/* Title row */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h1>Provenance Network</h1>
            <div className="subtitle">
              {data ? `${data.stats.total_nodes} nodes · ${data.stats.total_edges} connections` : 'Loading…'}
            </div>
          </div>
          <button className="btn btn-ghost" onClick={load}>↻ Refresh</button>
        </div>

        {/* Search bar */}
        <div style={{ marginTop: '0.75rem' }}>
          <input
            type="search"
            placeholder="Search nodes… (matching nodes light up)"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            style={{
              width: '100%',
              padding: '0.4rem 0.75rem',
              border: '1px solid var(--border)',
              borderRadius: '3px',
              fontFamily: 'var(--font-serif)',
              fontSize: '0.875rem',
              background: 'var(--cream-bg)',
              color: 'var(--text-body)',
              boxSizing: 'border-box',
              outline: 'none',
            }}
          />
        </div>

        {/* Type filter — doubles as the legend */}
        <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.6rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginRight: '0.1rem', flexShrink: 0 }}>
            Show:
          </span>
          {ALL_TYPES.map(type => {
            const active = activeTypes.has(type)
            return (
              <button
                key={type}
                onClick={() => toggleType(type)}
                title={active ? `Hide ${type} nodes` : `Show ${type} nodes`}
                style={{
                  display:    'flex',
                  alignItems: 'center',
                  gap:        '0.3rem',
                  padding:    '0.2rem 0.6rem',
                  border:     `1px solid ${active ? NODE_COLORS[type] : 'var(--border)'}`,
                  borderRadius: '12px',
                  background:   active ? `${NODE_COLORS[type]}1a` : 'transparent',
                  color:        active ? 'var(--text-body)' : 'var(--text-muted)',
                  fontSize:     '0.8rem',
                  cursor:       'pointer',
                  transition:   'all 0.15s',
                  fontFamily:   'var(--font-serif)',
                  opacity:      active ? 1 : 0.45,
                }}
              >
                <div style={{
                  width:        '8px',
                  height:       '8px',
                  borderRadius: type === 'document' ? '2px' : '50%',
                  background:   active ? NODE_COLORS[type] : 'var(--text-muted)',
                  flexShrink:   0,
                }} />
                {type.charAt(0).toUpperCase() + type.slice(1)}
              </button>
            )
          })}
          {/* "Show all" shortcut — only visible when something is hidden */}
          {activeTypes.size < ALL_TYPES.length && (
            <button
              onClick={() => setActiveTypes(new Set(ALL_TYPES))}
              style={{
                fontSize:       '0.75rem',
                color:          'var(--text-muted)',
                background:     'none',
                border:         'none',
                cursor:         'pointer',
                padding:        '0.2rem 0.25rem',
                textDecoration: 'underline',
                fontFamily:     'var(--font-serif)',
              }}
            >
              Show all
            </button>
          )}
        </div>
      </div>

      {/* Graph canvas */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {loading ? (
          <div className="loading" style={{ height: '100%' }}>Building network…</div>
        ) : !data || data.nodes.length === 0 ? (
          <div className="empty-state" style={{ paddingTop: '4rem' }}>
            <h3>No network data yet</h3>
            <p>Ingest photos to build the provenance network.</p>
          </div>
        ) : (
          <svg ref={svgRef} style={{ width: '100%', height: '100%', cursor: 'grab' }} />
        )}

        {/* Node detail panel */}
        {selected && (
          <div style={{
            position:   'absolute',
            top:        '1rem',
            right:      '1rem',
            width:      '260px',
            background: 'var(--cream-card)',
            border:     '1px solid var(--border)',
            borderRadius: '4px',
            padding:    '1rem',
            boxShadow:  '0 4px 12px var(--shadow-deep)',
            maxHeight:  'calc(100% - 2rem)',
            overflowY:  'auto',
          }}>
            <button
              onClick={() => setSelected(null)}
              style={{ float: 'right', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '1.1rem' }}
            >×</button>

            <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '0.25rem' }}>
              {selected.type}
            </div>
            <div style={{ fontWeight: 600, fontSize: '0.95rem', color: 'var(--navy)', marginBottom: '0.5rem' }}>
              {selected.label}
            </div>

            {/* Entity node — document list toggle */}
            {selected.doc_count !== undefined && (
              <div>
                <button
                  onClick={toggleDocs}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                    fontSize: '0.82rem', color: 'var(--navy-light)', fontWeight: 600,
                    display: 'flex', alignItems: 'center', gap: '0.3rem',
                  }}
                >
                  Appears in {selected.doc_count} document{selected.doc_count !== 1 ? 's' : ''}
                  <span style={{ fontSize: '0.7rem' }}>{docsExpanded ? '▲' : '▼'}</span>
                </button>

                {docsExpanded && (
                  <div style={{ marginTop: '0.5rem' }}>
                    {docsLoading ? (
                      <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>Loading…</div>
                    ) : entityDocs.length === 0 ? (
                      <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>No documents found</div>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                        {entityDocs.map(doc => (
                          <button
                            key={`${doc.record_type}-${doc.id}`}
                            onClick={() => doc.record_type === 'group' ? navigate(`/groups/${doc.id}`) : setPreviewDocId(doc.id)}
                            style={{
                              background: 'var(--cream-bg)',
                              border: '1px solid var(--border)',
                              borderRadius: '3px',
                              padding: '0.3rem 0.5rem',
                              fontSize: '0.8rem',
                              color: 'var(--navy)',
                              cursor: 'pointer',
                              textAlign: 'left',
                              fontFamily: 'inherit',
                              display: 'flex',
                              flexDirection: 'column',
                              gap: '1px',
                            }}
                            onMouseEnter={e => e.currentTarget.style.background = 'var(--cream-card)'}
                            onMouseLeave={e => e.currentTarget.style.background = 'var(--cream-bg)'}
                          >
                            <span style={{ fontWeight: 600, lineHeight: 1.3 }}>
                              {doc.record_type === 'group' && (
                                <span style={{ fontSize: '0.68rem', background: 'var(--navy)', color: 'white', borderRadius: '2px', padding: '0 3px', marginRight: '4px', verticalAlign: 'middle' }}>⊞</span>
                              )}
                              {(doc.title || doc.filename || `Document #${doc.id}`).substring(0, 36)}
                              {(doc.title || doc.filename || '').length > 36 ? '…' : ''}
                            </span>
                            {doc.date_depicted && (
                              <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>{doc.date_depicted}</span>
                            )}
                            {doc.role && (
                              <span style={{ color: 'var(--text-light)', fontSize: '0.75rem', fontStyle: 'italic' }}>{doc.role}</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Document preview panel */}
      {previewDocId != null && (
        <DocPreviewPanel docId={previewDocId} onClose={() => setPreviewDocId(null)} />
      )}
    </div>
  )
}

// ── D3 render function ────────────────────────────────────────────────────────
// Returns { applyVisualState(query, types) } so React can imperatively
// highlight or filter nodes without re-running the simulation.

function renderGraph(data, svgEl, setSelected) {
  const { nodes: rawNodes, edges: rawEdges } = data
  if (!rawNodes.length) return null

  const width  = svgEl.clientWidth  || 800
  const height = svgEl.clientHeight || 600

  d3.select(svgEl).selectAll('*').remove()

  const svg = d3.select(svgEl).attr('viewBox', [0, 0, width, height])
  const g   = svg.append('g')

  svg.call(
    d3.zoom().scaleExtent([0.1, 4]).on('zoom', ev => g.attr('transform', ev.transform))
  )

  // Clone nodes/edges so D3 can mutate them freely
  const nodes = rawNodes.map(n => ({ ...n }))
  const edges = rawEdges.map(e => ({ ...e }))

  const nodeById = {}
  for (const n of nodes) nodeById[n.id] = n

  const validEdges = edges.filter(e => nodeById[e.source] && nodeById[e.target])

  // ── Links ─────────────────────────────────────────────────────────────────
  const link = g.append('g')
    .selectAll('line')
    .data(validEdges)
    .join('line')
    .attr('stroke', '#d4c9a8')
    .attr('stroke-opacity', 0.6)
    .attr('stroke-width', d => Math.min(d.weight || 1, 4))

  // ── Node groups ───────────────────────────────────────────────────────────
  const node = g.append('g')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .attr('cursor', 'pointer')
    .on('click', (event, d) => { event.stopPropagation(); setSelected(d) })
    .call(
      d3.drag()
        .on('start', (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
        .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y })
        .on('end',   (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null })
    )

  // Draw shapes, icons, and labels
  node.each(function(d) {
    const el   = d3.select(this)
    const r    = NODE_RADIUS[d.type] || 8
    const fill = NODE_COLORS[d.type] || '#888'

    // 1. Background circle
    el.append('circle')
      .attr('r', r)
      .attr('fill', fill)
      .attr('stroke', 'white').attr('stroke-width', 1.5)

    // 2. Pictographic icon centered at (0,0)
    _drawNodeIcon(el, d.type, fill)

    // 3. Text label to the right of the node
    el.append('text')
      .text(d.label?.substring(0, 20) + (d.label?.length > 20 ? '…' : ''))
      .attr('x', r + 4).attr('y', 4)
      .attr('font-size', '10px')
      .attr('font-family', 'var(--font-serif)')
      .attr('fill', 'var(--text-body)')
      .style('pointer-events', 'none')
      .style('user-select', 'none')
  })

  node.append('title').text(d => d.label)

  svg.on('click', () => setSelected(null))

  // ── Force simulation ──────────────────────────────────────────────────────
  // The layout is warmed up off-screen: ticking without touching the DOM
  // avoids hundreds of full-graph SVG updates that froze the page. The DOM is
  // positioned once after warm-up, then again only while a node is dragged.
  function draw() {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y)
    node.attr('transform', d => `translate(${d.x},${d.y})`)
  }

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(validEdges).id(d => d.id).distance(d => 60 + 10 / (d.weight || 1)))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(20))
    .stop()

  for (let i = 0; i < 250; i++) simulation.tick()
  draw()

  // Keep drag interactive: the ticker only runs while the simulation is hot
  // (drag handlers call alphaTarget(0.3).restart()).
  simulation.on('tick', draw)

  // ── Visual-state API ──────────────────────────────────────────────────────
  // Called imperatively from React whenever the search query changes.
  // After the simulation runs, d.source / d.target in link data are node objects.

  function applyVisualState(query) {
    const q        = (query || '').trim().toLowerCase()
    const hasQuery = q.length > 0

    if (!hasQuery) {
      // Reset to default appearance
      node.style('opacity', 1)
      node.each(function() {
        d3.select(this).select('circle').attr('stroke', 'white').attr('stroke-width', 1.5)
      })
      link.attr('stroke-opacity', 0.6)
      return
    }

    // Dim non-matching nodes; illuminate matches with a gold ring
    node.style('opacity', d => d.label?.toLowerCase().includes(q) ? 1 : 0.1)
    node.each(function(d) {
      const matches = d.label?.toLowerCase().includes(q)
      d3.select(this).select('circle')
        .attr('stroke', matches ? '#c9a84c' : 'white')
        .attr('stroke-width', matches ? 3.5 : 1.5)
    })

    // Boost edges touching a matching node; fade the rest
    link.attr('stroke-opacity', d => {
      const src = typeof d.source === 'object' ? d.source : nodeById[d.source]
      const tgt = typeof d.target === 'object' ? d.target : nodeById[d.target]
      if (!src || !tgt) return 0
      const srcM = src.label?.toLowerCase().includes(q)
      const tgtM = tgt.label?.toLowerCase().includes(q)
      return (srcM || tgtM) ? 0.75 : 0.08
    })
  }

  return { applyVisualState }
}

// ── Node icon renderer ────────────────────────────────────────────────────────
// Draws a small SVG pictogram centered at (0,0) inside each node.
// nodeColor is the filled background colour, used for cut-out details.

function _drawNodeIcon(el, type, nodeColor) {
  const w = 'rgba(255,255,255,0.92)'  // white for icon fills

  switch (type) {

    // ── Person: head circle + shoulder arc ──────────────────────────────────
    case 'person': {
      el.append('circle')
        .attr('cx', 0).attr('cy', -4).attr('r', 2.8)
        .attr('fill', w).style('pointer-events', 'none')
      el.append('path')
        .attr('d', 'M -5 7 C -5 1.5 5 1.5 5 7 Z')
        .attr('fill', w).style('pointer-events', 'none')
      break
    }

    // ── Institution: triangular roof + columned building ────────────────────
    case 'institution': {
      // Roof
      el.append('polygon')
        .attr('points', '0,-8 -7,-2 7,-2')
        .attr('fill', w).style('pointer-events', 'none')
      // Building body
      el.append('rect')
        .attr('x', -6).attr('y', -2).attr('width', 12).attr('height', 9)
        .attr('fill', w).style('pointer-events', 'none')
      // Door (cut-out in node colour)
      el.append('rect')
        .attr('x', -2).attr('y', 3).attr('width', 4).attr('height', 4)
        .attr('fill', nodeColor).style('pointer-events', 'none')
      // Two columns (cut-out in node colour)
      ;[[-5, 1.5], [3.5, 1.5]].forEach(([x, cw]) =>
        el.append('rect')
          .attr('x', x).attr('y', -2).attr('width', cw).attr('height', 9)
          .attr('fill', nodeColor).style('pointer-events', 'none')
      )
      break
    }

    // ── Object: 3-D box with ribbon lines ───────────────────────────────────
    case 'object': {
      // Front face
      el.append('rect')
        .attr('x', -4.5).attr('y', -2).attr('width', 9).attr('height', 7)
        .attr('rx', 1).attr('fill', w).style('pointer-events', 'none')
      // Top face (slightly dimmer for depth)
      el.append('path')
        .attr('d', 'M -4.5 -2 L 0 -6 L 4.5 -2 Z')
        .attr('fill', 'rgba(255,255,255,0.62)').style('pointer-events', 'none')
      // Ribbon lines to suggest a wrapped package
      el.append('line')
        .attr('x1', 0).attr('y1', -6).attr('x2', 0).attr('y2', 5)
        .attr('stroke', nodeColor).attr('stroke-width', 1).attr('stroke-opacity', 0.4)
        .style('pointer-events', 'none')
      el.append('line')
        .attr('x1', -4.5).attr('y1', 1.5).attr('x2', 4.5).attr('y2', 1.5)
        .attr('stroke', nodeColor).attr('stroke-width', 1).attr('stroke-opacity', 0.4)
        .style('pointer-events', 'none')
      break
    }

    // ── Document: text lines (rect shape is already drawn by parent) ────────
    case 'document': {
      // Three horizontal lines suggesting text content
      ;[[-2.5, 4.5], [0, 4.5], [2.5, 2]].forEach(([y, x2]) =>
        el.append('line')
          .attr('x1', -4.5).attr('y1', y).attr('x2', x2).attr('y2', y)
          .attr('stroke', 'rgba(255,255,255,0.7)').attr('stroke-width', 1.3)
          .style('pointer-events', 'none')
      )
      break
    }

    // ── Unknown: question mark ───────────────────────────────────────────────
    case 'unknown':
    default: {
      el.append('text')
        .attr('x', 0).attr('y', 4)
        .attr('text-anchor', 'middle')
        .attr('font-size', '11px')
        .attr('fill', w)
        .attr('font-weight', 700)
        .style('pointer-events', 'none')
        .style('user-select', 'none')
        .text('?')
      break
    }
  }
}
