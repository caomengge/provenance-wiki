import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'
import EntityNameAutocomplete from '../components/EntityNameAutocomplete'

const TYPE_COLORS = {
  person:      { bg: '#2563eb22', border: '#2563eb66', text: '#1d4ed8' },
  object:      { bg: '#d9770622', border: '#d9770666', text: '#b45309' },
  institution: { bg: '#16a34a22', border: '#16a34a66', text: '#15803d' },
  unknown:     { bg: '#6b728022', border: '#6b728066', text: '#4b5563' },
}

const ENTITY_TYPES = ['person', 'object', 'institution', 'unknown']

function TypeBadge({ type }) {
  const c = TYPE_COLORS[type] || TYPE_COLORS.unknown
  return (
    <span style={{
      background: c.bg,
      border: `1px solid ${c.border}`,
      color: c.text,
      borderRadius: '3px',
      padding: '0.1rem 0.45rem',
      fontSize: '0.75rem',
      fontWeight: 600,
      textTransform: 'capitalize',
      whiteSpace: 'nowrap',
    }}>
      {type}
    </span>
  )
}

/** Inline edit row – shown when editing */
function EditRow({ entity, onSave, onCancel, onMerge, onDelete }) {
  const [name, setName] = useState(entity.name)
  const [type, setType] = useState(entity.type)
  const [saving, setSaving] = useState(false)
  const [mergeOpen, setMergeOpen] = useState(false)
  const [mergeTarget, setMergeTarget] = useState('')      // id (as string) of picked target
  const [mergeTargetText, setMergeTargetText] = useState('') // current typed text
  const [mergeTargetType, setMergeTargetType] = useState('')
  const [merging, setMerging] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState('')
  const [aliasesOpen, setAliasesOpen] = useState(false)
  const [aliases, setAliases] = useState([])
  const [aliasInput, setAliasInput] = useState('')
  const [aliasBusy, setAliasBusy] = useState(false)

  const loadAliases = useCallback(async () => {
    try {
      const res = await api.getEntity(entity.id)
      setAliases(res.aliases || [])
    } catch (err) {
      setError(err.message)
    }
  }, [entity.id])

  const handleAddAlias = async () => {
    const name = aliasInput.trim()
    if (!name) return
    setAliasBusy(true)
    setError('')
    try {
      await api.addEntityAlias(entity.id, name)
      setAliasInput('')
      await loadAliases()
    } catch (err) {
      setError(err.message)
    } finally {
      setAliasBusy(false)
    }
  }

  const handleRemoveAlias = async (aliasId) => {
    setAliasBusy(true)
    setError('')
    try {
      await api.removeEntityAlias(entity.id, aliasId)
      await loadAliases()
    } catch (err) {
      setError(err.message)
    } finally {
      setAliasBusy(false)
    }
  }

  const handleSave = async () => {
    if (!name.trim()) { setError('Name is required'); return }
    setSaving(true)
    setError('')
    try {
      await onSave(entity.id, { name: name.trim(), type })
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleMerge = async () => {
    if (!mergeTarget) return
    setMerging(true)
    setError('')
    try {
      // "Merge this entity INTO target" → keep target, discard this entity.
      await onMerge(parseInt(mergeTarget, 10), entity.id)
    } catch (err) {
      setError(err.message)
      setMerging(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    setError('')
    try {
      await onDelete(entity.id)
    } catch (err) {
      setError(err.message)
      setDeleting(false)
    }
  }

  return (
    <>
    <tr style={{ background: 'var(--cream-bg)' }}>
      <td style={{ padding: '0.6rem 0.75rem' }}>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleSave(); if (e.key === 'Escape') onCancel() }}
          style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'var(--font-serif)', fontSize: '0.9rem' }}
          autoFocus
        />
      </td>
      <td style={{ padding: '0.6rem 0.75rem' }}>
        <select
          value={type}
          onChange={e => setType(e.target.value)}
          style={{ fontFamily: 'var(--font-serif)', fontSize: '0.85rem' }}
        >
          {ENTITY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </td>
      <td style={{ padding: '0.6rem 0.75rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
        {entity.doc_count}
      </td>
      <td style={{ padding: '0.6rem 0.75rem', whiteSpace: 'nowrap' }}>
        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving} style={{ fontSize: '0.82rem', padding: '0.25rem 0.6rem' }}>
            {saving ? '…' : 'Save'}
          </button>
          <button className="btn btn-ghost" onClick={onCancel} style={{ fontSize: '0.82rem', padding: '0.25rem 0.6rem' }}>
            Cancel
          </button>
          <button
            className="btn btn-ghost"
            onClick={() => { setMergeOpen(v => !v); setConfirmDelete(false) }}
            style={{ fontSize: '0.82rem', padding: '0.25rem 0.6rem', color: 'var(--text-muted)' }}
          >
            Merge into…
          </button>
          <button
            className="btn btn-ghost"
            onClick={() => {
              setAliasesOpen(v => {
                if (!v) loadAliases()
                return !v
              })
              setConfirmDelete(false)
            }}
            style={{ fontSize: '0.82rem', padding: '0.25rem 0.6rem', color: 'var(--text-muted)' }}
          >
            Aliases…
          </button>
          {!confirmDelete ? (
            <button
              className="btn btn-ghost"
              onClick={() => { setConfirmDelete(true); setMergeOpen(false) }}
              style={{ fontSize: '0.82rem', padding: '0.25rem 0.6rem', color: '#b91c1c' }}
            >
              Delete
            </button>
          ) : (
            <span style={{ display: 'inline-flex', gap: '0.3rem', alignItems: 'center' }}>
              <span style={{ fontSize: '0.78rem', color: '#b91c1c' }}>Confirm?</span>
              <button
                className="btn btn-ghost"
                onClick={handleDelete}
                disabled={deleting}
                style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem', color: '#b91c1c', border: '1px solid #b91c1c44' }}
              >
                {deleting ? '…' : 'Yes, delete'}
              </button>
              <button
                className="btn btn-ghost"
                onClick={() => setConfirmDelete(false)}
                style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem' }}
              >
                No
              </button>
            </span>
          )}
        </div>
        {error && <div style={{ marginTop: '0.3rem', fontSize: '0.78rem', color: '#b91c1c' }}>{error}</div>}
      </td>
    </tr>
    {mergeOpen && (
      <tr style={{ background: 'var(--cream-bg)' }}>
        <td colSpan={4} style={{ padding: '0 0.75rem 0.8rem', borderTop: 'none' }}>
          <div style={{
            display: 'flex',
            gap: '0.5rem',
            alignItems: 'flex-start',
            whiteSpace: 'normal',
            background: 'var(--cream-card, #faf7f2)',
            border: '1px solid var(--border)',
            borderRadius: '4px',
            padding: '0.6rem 0.75rem',
          }}>
            <div style={{ flex: 1, minWidth: 0, position: 'relative' }}>
              <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.25rem' }}>
                Merge <strong>{entity.name}</strong> into…
              </label>
              <EntityNameAutocomplete
                value={mergeTargetText}
                onChange={(text) => {
                  setMergeTargetText(text)
                  setMergeTarget('')
                  setMergeTargetType('')
                }}
                onPick={(e) => {
                  if (e.id === entity.id) return
                  setMergeTarget(String(e.id))
                  setMergeTargetText(e.name)
                  setMergeTargetType(e.type)
                }}
                placeholder="Type to search existing entities…"
                style={{ width: '100%' }}
              />
              {mergeTarget && mergeTargetType && (
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.25rem', display: 'block' }}>
                  Will merge into: <strong>{mergeTargetText}</strong> ({mergeTargetType})
                </span>
              )}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', marginTop: '1.1rem' }}>
              <button
                className="btn btn-primary"
                onClick={handleMerge}
                disabled={!mergeTarget || merging}
                style={{ fontSize: '0.82rem', padding: '0.3rem 0.8rem' }}
              >
                {merging ? '…' : 'Merge'}
              </button>
              <button
                className="btn btn-ghost"
                onClick={() => { setMergeOpen(false); setMergeTarget(''); setMergeTargetText(''); setMergeTargetType('') }}
                style={{ fontSize: '0.78rem', padding: '0.2rem 0.6rem' }}
              >
                Cancel
              </button>
            </div>
          </div>
        </td>
      </tr>
    )}
    {aliasesOpen && (
      <tr style={{ background: 'var(--cream-bg)' }}>
        <td colSpan={4} style={{ padding: '0 0.75rem 0.8rem', borderTop: 'none' }}>
          <div style={{
            whiteSpace: 'normal',
            background: 'var(--cream-card, #faf7f2)',
            border: '1px solid var(--border)',
            borderRadius: '4px',
            padding: '0.6rem 0.75rem',
          }}>
            <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.4rem' }}>
              Alternate names for <strong>{entity.name}</strong>
            </label>
            {aliases.length === 0 && (
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.4rem' }}>
                No aliases yet.
              </div>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', marginBottom: '0.5rem' }}>
              {aliases.map(a => (
                <span key={a.id} style={{
                  display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
                  background: 'white', border: '1px solid var(--border)',
                  borderRadius: '3px', padding: '0.15rem 0.45rem', fontSize: '0.8rem',
                }}>
                  {a.name}
                  <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
                    ({a.source})
                  </span>
                  <button
                    onClick={() => handleRemoveAlias(a.id)}
                    disabled={aliasBusy}
                    title="Remove alias"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: '#b91c1c', fontSize: '0.85rem', lineHeight: 1 }}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <input
                value={aliasInput}
                onChange={e => setAliasInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleAddAlias() }}
                placeholder="Add an alternate name…"
                style={{ flex: 1, fontFamily: 'var(--font-serif)', fontSize: '0.85rem' }}
              />
              <button
                className="btn btn-primary"
                onClick={handleAddAlias}
                disabled={aliasBusy || !aliasInput.trim()}
                style={{ fontSize: '0.82rem', padding: '0.3rem 0.8rem' }}
              >
                {aliasBusy ? '…' : 'Add'}
              </button>
            </div>
          </div>
        </td>
      </tr>
    )}
    </>
  )
}

export default function Entities() {
  const navigate = useNavigate()
  const [entities, setEntities]       = useState([])
  const [total, setTotal]             = useState(0)
  const [page, setPage]               = useState(1)
  const [loading, setLoading]         = useState(false)
  const [q, setQ]                     = useState('')
  const [typeFilter, setTypeFilter]   = useState('')
  const [editingId, setEditingId]     = useState(null)

  const PER_PAGE = 50

  const load = useCallback(async (p = 1, query = q, type = typeFilter) => {
    setLoading(true)
    try {
      const params = { page: p, per_page: PER_PAGE }
      if (query)  params.q    = query
      if (type)   params.type = type
      const res = await api.getEntities(params)
      setEntities(res.entities || [])
      setTotal(res.total || 0)
      setPage(p)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [q, typeFilter])

  useEffect(() => { load(1) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearch = (e) => {
    e.preventDefault()
    setEditingId(null)
    load(1, q, typeFilter)
  }

  const handleTypeFilter = (type) => {
    setTypeFilter(type)
    setEditingId(null)
    load(1, q, type)
  }

  const handleSave = async (id, data) => {
    await api.updateEntity(id, data)
    setEditingId(null)
    load(page, q, typeFilter)
  }

  const handleDelete = async (id) => {
    await api.deleteEntity(id)
    setEditingId(null)
    load(page, q, typeFilter)
  }

  const handleMerge = async (keepId, discardId) => {
    await api.mergeEntities(keepId, discardId)
    setEditingId(null)
    load(page, q, typeFilter)
  }

  const totalPages = Math.ceil(total / PER_PAGE)

  return (
    <div style={{ padding: '2rem', maxWidth: '900px' }}>
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontFamily: 'var(--font-serif)', fontSize: '1.5rem', color: 'var(--navy)', margin: '0 0 0.25rem' }}>
          Entities
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.88rem', margin: 0 }}>
          {total} entities — edit attributes, merge duplicates, or delete entries.
        </p>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.25rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <form onSubmit={handleSearch} style={{ display: 'flex', gap: '0.5rem', flex: 1, minWidth: '180px' }}>
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search by name…"
            style={{ flex: 1, fontFamily: 'var(--font-serif)', fontSize: '0.88rem' }}
          />
          <button type="submit" className="btn btn-primary" style={{ fontSize: '0.85rem', padding: '0.3rem 0.75rem' }}>
            Search
          </button>
        </form>
        <div style={{ display: 'flex', gap: '0.35rem' }}>
          {['', ...ENTITY_TYPES].map(t => (
            <button
              key={t || 'all'}
              onClick={() => handleTypeFilter(t)}
              style={{
                padding: '0.3rem 0.65rem',
                fontSize: '0.8rem',
                borderRadius: '3px',
                border: '1px solid',
                cursor: 'pointer',
                fontFamily: 'var(--font-serif)',
                ...(typeFilter === t
                  ? { background: 'var(--navy)', color: 'var(--cream-light)', borderColor: 'var(--navy)' }
                  : { background: 'transparent', color: 'var(--text-muted)', borderColor: 'var(--border)' }
                ),
              }}
            >
              {t || 'All'}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div style={{ border: '1px solid var(--border)', borderRadius: '4px', overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-serif)' }}>
          <thead>
            <tr style={{ background: 'var(--navy)', color: 'var(--cream-light)' }}>
              <th style={{ padding: '0.6rem 0.75rem', textAlign: 'left', fontSize: '0.8rem', fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase' }}>Name</th>
              <th style={{ padding: '0.6rem 0.75rem', textAlign: 'left', fontSize: '0.8rem', fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', width: '120px' }}>Type</th>
              <th style={{ padding: '0.6rem 0.75rem', textAlign: 'right', fontSize: '0.8rem', fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', width: '80px' }}>Docs</th>
              <th style={{ padding: '0.6rem 0.75rem', textAlign: 'right', fontSize: '0.8rem', fontWeight: 600, letterSpacing: '0.05em', textTransform: 'uppercase', width: '80px' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={4} style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                  Loading…
                </td>
              </tr>
            )}
            {!loading && entities.length === 0 && (
              <tr>
                <td colSpan={4} style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                  No entities found.
                </td>
              </tr>
            )}
            {!loading && entities.map((entity, i) => (
              editingId === entity.id
                ? (
                  <EditRow
                    key={entity.id}
                    entity={entity}
                    onSave={handleSave}
                    onCancel={() => setEditingId(null)}
                    onMerge={handleMerge}
                    onDelete={handleDelete}
                  />
                ) : (
                  <tr
                    key={entity.id}
                    style={{
                      background: i % 2 === 0 ? 'white' : 'var(--cream-bg)',
                      borderTop: '1px solid var(--border)',
                    }}
                  >
                    <td style={{ padding: '0.55rem 0.75rem', fontSize: '0.9rem' }}>
                      <button
                        onClick={() => navigate(`/search?entity_id=${entity.id}`)}
                        style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--navy)', fontFamily: 'var(--font-serif)', fontSize: '0.9rem', textAlign: 'left' }}
                        title="View documents mentioning this entity"
                      >
                        {entity.name}
                      </button>
                      {entity.aliases && (
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}
                             title="Alternate names / aliases">
                          a.k.a. {entity.aliases}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: '0.55rem 0.75rem' }}>
                      <TypeBadge type={entity.type} />
                    </td>
                    <td style={{ padding: '0.55rem 0.75rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                      {entity.doc_count}
                    </td>
                    <td style={{ padding: '0.55rem 0.75rem', textAlign: 'right' }}>
                      <button
                        onClick={() => setEditingId(entity.id)}
                        className="btn btn-ghost"
                        style={{ fontSize: '0.8rem', padding: '0.2rem 0.55rem' }}
                      >
                        Edit
                      </button>
                    </td>
                  </tr>
                )
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'center', marginTop: '1.25rem', alignItems: 'center' }}>
          <button
            className="btn btn-ghost"
            onClick={() => load(page - 1)}
            disabled={page <= 1}
            style={{ fontSize: '0.85rem', padding: '0.3rem 0.7rem' }}
          >
            ← Prev
          </button>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            Page {page} of {totalPages}
          </span>
          <button
            className="btn btn-ghost"
            onClick={() => load(page + 1)}
            disabled={page >= totalPages}
            style={{ fontSize: '0.85rem', padding: '0.3rem 0.7rem' }}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}
