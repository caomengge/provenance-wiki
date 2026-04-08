import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import DocumentCard from '../components/DocumentCard'
import BatchEditBar from '../components/BatchEditBar'
import api from '../api/client'

const SORT_OPTIONS = [
  { value: 'created_at',    label: 'Date Added' },
  { value: 'date_depicted', label: 'Document Date' },
  { value: 'title',         label: 'Title' },
]

export default function Gallery({ onStatsUpdate }) {
  const [docs, setDocs]               = useState([])
  const [total, setTotal]             = useState(0)
  const [page, setPage]               = useState(1)
  const [loading, setLoading]         = useState(true)
  const [view, setView]               = useState('grid')
  const [sort, setSort]               = useState('created_at')
  const [order, setOrder]             = useState('desc')
  const [keyOnly, setKeyOnly]         = useState(false)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [selectMode, setSelectMode]   = useState(false)
  const [exporting, setExporting]     = useState(false)

  // Filter options
  const [tags,          setTags]          = useState([])
  const [filterTag,     setFilterTag]     = useState('')
  const [archives,      setArchives]      = useState([])
  const [filterArchive, setFilterArchive] = useState('')
  const [entities,      setEntities]      = useState([])
  const [filterEntity,  setFilterEntity]  = useState('')

  const [perPage, setPerPage] = useState(50)

  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, per_page: perPage === 'all' ? 9999 : perPage, sort, order }
      if (keyOnly)       params.key_evidence   = 'true'
      if (filterTag)     params.tag_id         = filterTag
      if (filterArchive) params.source_archive = filterArchive
      if (filterEntity)  params.entity_id      = filterEntity

      const [docsRes, groupsRes] = await Promise.all([
        api.getDocuments(params),
        api.getGroups({ page, per_page: perPage === 'all' ? 9999 : perPage }),
      ])

      const groups = (groupsRes.groups || []).map(g => ({ ...g, _isGroup: true }))
      const combined = [...groups, ...(docsRes.documents || [])]
      combined.sort((a, b) => new Date(b.created_at) - new Date(a.created_at))

      setDocs(combined)
      setTotal((docsRes.total || 0) + (groupsRes.total || 0))
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [page, perPage, sort, order, keyOnly, filterTag, filterArchive, filterEntity])

  useEffect(() => { load() }, [load])

  // Load filter options once
  useEffect(() => {
    api.getTags().then(r => setTags(r.tags || [])).catch(() => {})
    api.getArchives().then(r => setArchives(r.archives || [])).catch(() => {})
    api.getEntities({ per_page: 500 }).then(r => setEntities(r.entities || [])).catch(() => {})
  }, [])

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

  // Select / deselect all visible docs
  const toggleSelectAll = () => {
    if (selectedIds.size === docs.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(docs.map(d => d.id)))
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

  const totalPages = perPage === 'all' ? 1 : Math.ceil(total / perPage)

  return (
    <div>
      <div className="page-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
          <div>
            <h1>Document Gallery</h1>
            <div className="subtitle">{total} document{total !== 1 ? 's' : ''} in the archive</div>
          </div>

          {/* Header action area */}
          <div style={{ flex: 1, minWidth: '260px' }}>
            {selectMode ? (
              <BatchEditBar
                selectedIds={selectedIds}
                onClear={exitSelectMode}
                onExport={exportSelection}
                exporting={exporting}
                archives={archives}
                onDone={() => { load(); if (onStatsUpdate) api.getStats().then(onStatsUpdate).catch(() => {}) }}
              />
            ) : (
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button
                  className="btn btn-ghost"
                  onClick={() => setSelectMode(true)}
                  title="Select documents to export or batch-edit"
                >
                  ☐ Select
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Select-all row (only in selectMode) */}
        {selectMode && (
          <div style={{ marginTop: '0.4rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button
              className="btn btn-ghost"
              onClick={toggleSelectAll}
              style={{ fontSize: '0.82rem', padding: '0.2rem 0.6rem' }}
            >
              {selectedIds.size === docs.length ? '☑ Deselect all' : '☐ Select all on page'}
            </button>
          </div>
        )}

        {/* Filter bar */}
        <div style={{ display: 'flex', gap: '0.6rem', marginTop: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <select value={sort} onChange={e => { setSort(e.target.value); setPage(1) }} style={{ width: 'auto' }}>
            {SORT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <select value={order} onChange={e => { setOrder(e.target.value); setPage(1) }} style={{ width: 'auto' }}>
            <option value="desc">Newest First</option>
            <option value="asc">Oldest First</option>
          </select>
          <select value={filterTag} onChange={e => { setFilterTag(e.target.value); setPage(1) }} style={{ width: 'auto' }}>
            <option value="">All Tags</option>
            {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <select value={filterArchive} onChange={e => { setFilterArchive(e.target.value); setPage(1) }} style={{ width: 'auto' }}>
            <option value="">All Sources</option>
            <option value="__none__">— No Source</option>
            {archives.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
          <select value={filterEntity} onChange={e => { setFilterEntity(e.target.value); setPage(1) }} style={{ width: 'auto' }}>
            <option value="">All Entities</option>
            {entities.map(e => <option key={e.id} value={e.id}>{e.name} ({e.type})</option>)}
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', textTransform: 'none', fontWeight: 400, fontSize: '0.9rem', cursor: 'pointer', marginBottom: 0 }}>
            <input type="checkbox" checked={keyOnly} onChange={e => { setKeyOnly(e.target.checked); setPage(1) }} />
            Key evidence only
          </label>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
            <button
              className="btn btn-ghost"
              onClick={() => setView('grid')}
              style={{ padding: '0.3rem 0.6rem', background: view === 'grid' ? 'var(--navy)' : undefined, color: view === 'grid' ? 'var(--cream-light)' : undefined }}
            >▤</button>
            <button
              className="btn btn-ghost"
              onClick={() => setView('list')}
              style={{ padding: '0.3rem 0.6rem', background: view === 'list' ? 'var(--navy)' : undefined, color: view === 'list' ? 'var(--cream-light)' : undefined }}
            >☰</button>
          </div>
        </div>
      </div>

      <div style={{ padding: '1.5rem 2rem' }}>
        {loading ? (
          <div className="loading">Loading documents…</div>
        ) : docs.length === 0 ? (
          <div className="empty-state">
            <h3>No documents in the archive yet.</h3>
            <p>Drop your photos into the <code>photos/</code> folder and click <strong>⊕ Ingest Photos</strong> in the sidebar.</p>
          </div>
        ) : view === 'grid' ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
            {docs.map(doc => (
              <DocumentCard
                key={doc.id}
                doc={doc}
                view="grid"
                selectMode={selectMode}
                selected={selectedIds.has(doc.id)}
                onToggleSelect={toggleSelect}
              />
            ))}
          </div>
        ) : (
          <div>
            {docs.map(doc => (
              <DocumentCard
                key={doc.id}
                doc={doc}
                view="list"
                selectMode={selectMode}
                selected={selectedIds.has(doc.id)}
                onToggleSelect={toggleSelect}
              />
            ))}
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem', marginTop: '1.5rem' }}>
          {/* Pagination (hidden when showing all or only one page) */}
          {totalPages > 1 ? (
            <div className="pagination" style={{ margin: 0 }}>
              <button className="btn btn-ghost" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>← Prev</button>
              <span className="current">Page {page} of {totalPages}</span>
              <button className="btn btn-ghost" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}>Next →</button>
            </div>
          ) : (
            <div />
          )}

          {/* Per-page selector */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.88rem', color: 'var(--text-muted)' }}>
            <span>Show</span>
            <select
              value={perPage}
              onChange={e => {
                const v = e.target.value === 'all' ? 'all' : Number(e.target.value)
                setPerPage(v)
                setPage(1)
              }}
              style={{ width: 'auto', fontSize: '0.88rem', padding: '0.2rem 0.4rem' }}
            >
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={250}>250</option>
              <option value="all">All</option>
            </select>
            <span>per page</span>
          </div>
        </div>
      </div>
    </div>
  )
}
