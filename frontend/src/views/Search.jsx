import React, { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import DocumentCard from '../components/DocumentCard'
import BatchEditBar from '../components/BatchEditBar'
import api from '../api/client'

export default function Search() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [query, setQuery]     = useState(searchParams.get('q') || '')
  const [mode, setMode]       = useState('keyword')
  const [results, setResults] = useState([])
  const [total, setTotal]     = useState(0)
  const [page, setPage]       = useState(1)
  const [loading, setLoading] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)

  // Filter options
  const [filterEntity,     setFilterEntity]     = useState(searchParams.get('entity_id') || '')
  const [filterEntityName, setFilterEntityName] = useState('')
  const [archives,         setArchives]         = useState([])
  const [filterArchive,    setFilterArchive]    = useState('')

  // Selection / batch-edit
  const [selectMode,   setSelectMode]   = useState(false)
  const [selectedIds,  setSelectedIds]  = useState(new Set())
  const [exporting,    setExporting]    = useState(false)

  const PER_PAGE = 20

  // Load filter option lists once
  useEffect(() => {
    api.getArchives().then(r => setArchives(r.archives || [])).catch(() => {})
  }, [])

  // Fetch the entity name when filterEntity is set via URL (e.g. clicking an
  // entity card elsewhere) so we can show it in the active-filter chip.
  useEffect(() => {
    if (!filterEntity) { setFilterEntityName(''); return }
    api.getEntity(filterEntity)
      .then(e => setFilterEntityName(e?.name || ''))
      .catch(() => setFilterEntityName(''))
  }, [filterEntity])

  const doSearch = useCallback(async (q, p = 1) => {
    if (!q.trim() && !filterEntity && !filterArchive) return
    setLoading(true)
    setHasSearched(true)
    try {
      const params = { q, mode, page: p, per_page: PER_PAGE }
      if (filterEntity)  params.entity_id      = filterEntity
      if (filterArchive) params.source_archive = filterArchive
      const res = await api.search(params)
      setResults(res.results || [])
      setTotal(res.total || 0)
      setPage(p)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [mode, filterEntity, filterArchive])

  // Auto-run if URL has q or entity_id
  useEffect(() => {
    const q   = searchParams.get('q') || ''
    const eid = searchParams.get('entity_id') || ''
    if (q || eid) {
      setQuery(q)
      setFilterEntity(eid)
      doSearch(q, 1)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Re-run search whenever an active filter changes
  useEffect(() => {
    if (filterEntity || filterArchive) {
      doSearch(query, 1)
    }
  }, [filterEntity, filterArchive, doSearch]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSubmit = (e) => {
    e.preventDefault()
    setSearchParams({ q: query })
    doSearch(query, 1)
  }

  // ── Selection helpers ────────────────────────────────────────────────────────

  const toggleSelect = (docId) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(docId)) next.delete(docId)
      else next.add(docId)
      return next
    })
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === results.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(results.map(d => (d.record_type === 'group' ? 'g:' : 'd:') + d.id)))
    }
  }

  const exportSelection = async () => {
    if (selectedIds.size === 0) return
    setExporting(true)
    try {
      const form   = document.createElement('form')
      form.method  = 'POST'
      form.action  = '/api/export/selection'
      form.target  = '_blank'
      const input  = document.createElement('input')
      input.type   = 'hidden'
      input.name   = 'doc_ids'
      input.value  = JSON.stringify([...selectedIds])
      form.appendChild(input)
      document.body.appendChild(form)
      form.submit()
      document.body.removeChild(form)
    } finally {
      setExporting(false)
    }
  }

  const totalPages = Math.ceil(total / PER_PAGE)

  // Active filter labels for result summary
  const activeArchiveLabel = filterArchive || null
  const activeEntityLabel  = filterEntity ? (filterEntityName || `#${filterEntity}`) : null

  return (
    <div>
      <div className="page-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h1>Search Archive</h1>
            <div className="subtitle">Full-text and semantic search across all provenance documents</div>
          </div>

          {/* Select-mode toggle (only when there are results) */}
          {hasSearched && results.length > 0 && !selectMode && (
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                className="btn btn-ghost"
                onClick={() => setSelectMode(true)}
                title="Select results to export or batch-edit"
              >
                ☐ Select
              </button>
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} style={{ marginTop: '1rem' }}>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <input
              type="search"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search by name, date, location, artwork title…"
              style={{ flex: 1, minWidth: '260px', fontSize: '1rem' }}
              autoFocus
            />
            <button type="submit" className="btn btn-primary">Search</button>
          </div>

          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ display: 'flex', gap: '0.25rem' }}>
              {['keyword', 'semantic'].map(m => (
                <button
                  key={m}
                  type="button"
                  className={`btn ${mode === m ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setMode(m)}
                  style={{ fontSize: '0.82rem', padding: '0.3rem 0.7rem', textTransform: 'capitalize' }}
                >
                  {m}
                </button>
              ))}
            </div>

            <select value={filterArchive} onChange={e => setFilterArchive(e.target.value)} style={{ width: 'auto' }}>
              <option value="">All Sources</option>
              <option value="__none__">— No Source</option>
              {archives.map(a => <option key={a} value={a}>{a}</option>)}
            </select>

            {filterEntity && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
                background: 'var(--cream-bg)', border: '1px solid var(--border)',
                borderRadius: '3px', padding: '0.25rem 0.5rem', fontSize: '0.85rem',
              }}>
                Entity: {filterEntityName || `#${filterEntity}`}
                <button
                  type="button"
                  onClick={() => setFilterEntity('')}
                  aria-label="Clear entity filter"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }}
                >×</button>
              </span>
            )}
          </div>
        </form>

        {/* Batch-edit bar (only in select mode) */}
        {selectMode && (
          <div style={{ marginTop: '0.75rem' }}>
            <BatchEditBar
              selectedIds={selectedIds}
              onClear={exitSelectMode}
              onExport={exportSelection}
              exporting={exporting}
              archives={archives}
              onDone={() => doSearch(query, page)}
            />
            <div style={{ marginTop: '0.4rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <button
                className="btn btn-ghost"
                onClick={toggleSelectAll}
                style={{ fontSize: '0.82rem', padding: '0.2rem 0.6rem' }}
              >
                {selectedIds.size === results.length ? '☑ Deselect all' : '☐ Select all on page'}
              </button>
            </div>
          </div>
        )}
      </div>

      <div style={{ padding: '1.5rem 2rem' }}>
        {loading ? (
          <div className="loading">Searching…</div>
        ) : !hasSearched ? (
          <div className="empty-state">
            <h3>Enter a search query or select a filter above</h3>
            <p>Search supports English, Chinese, German, French, and other languages. Try searching for a person's name, artwork title, auction house, or date. You can also filter by source archive, or click an entity from the Entities page to filter by it.</p>
          </div>
        ) : results.length === 0 ? (
          <div className="empty-state">
            <h3>No results found</h3>
            <p>Try different keywords or switch to semantic search mode.</p>
          </div>
        ) : (
          <>
            <div style={{ marginBottom: '1rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
              {total} result{total !== 1 ? 's' : ''}
              {query && <> for "<strong>{query}</strong>"</>}
              {activeEntityLabel  && ` · entity: ${activeEntityLabel}`}
              {activeArchiveLabel && ` · archive: ${activeArchiveLabel}`}
            </div>

            {results.map(doc => {
              const isGroup = doc.record_type === 'group'
              const cardDoc = isGroup ? { ...doc, _isGroup: true } : doc
              const key     = (isGroup ? 'g:' : 'd:') + doc.id
              return (
                <DocumentCard
                  key={key}
                  doc={cardDoc}
                  view="list"
                  selectMode={selectMode}
                  selected={selectedIds.has(key)}
                  onToggleSelect={toggleSelect}
                />
              )
            })}

            {totalPages > 1 && (
              <div className="pagination">
                <button className="btn btn-ghost" onClick={() => doSearch(query, page - 1)} disabled={page === 1}>← Prev</button>
                <span className="current">Page {page} of {totalPages}</span>
                <button className="btn btn-ghost" onClick={() => doSearch(query, page + 1)} disabled={page === totalPages}>Next →</button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
