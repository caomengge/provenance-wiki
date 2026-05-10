import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import api from '../api/client'
import EntityTag from '../components/EntityTag'
import AnnotationPanel from '../components/AnnotationPanel'
import TagManager from '../components/TagManager'
import InlineEdit from '../components/InlineEdit'
import EvidenceFlag from '../components/EvidenceFlag'
import { useJobStatus } from '../JobStatus'
import TransactionEditor from '../components/TransactionEditor'
import EntityNameAutocomplete from '../components/EntityNameAutocomplete'
import TranscriptionEditor from '../components/TranscriptionEditor'
import { MEDIUM_CATEGORIES } from '../constants/medium'

export default function GroupDetail() {
  const { id }     = useParams()
  const navigate   = useNavigate()
  const { setStatus } = useJobStatus()
  const [group, setGroup]       = useState(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState('')

  // Re-extract
  const [reExtracting, setReExtracting] = useState(false)

  // Delete
  const [deleteModalOpen, setDeleteModalOpen] = useState(false)
  const [deleting, setDeleting]               = useState(false)

  // Entity management
  const [addingEntity,  setAddingEntity]  = useState(false)
  const [newEntityName, setNewEntityName] = useState('')
  const [newEntityType, setNewEntityType] = useState('person')
  const [newEntityRole, setNewEntityRole] = useState('')
  const [entitySaving,  setEntitySaving]  = useState(false)
  const entityNameRef = useRef(null)

  // Transcription modal
  const [showTranscription, setShowTranscription] = useState(false)

  // Page reorder drag state
  const [pageOrder, setPageOrder]     = useState([])
  const [dragIdx, setDragIdx]         = useState(null)
  const [imgCacheBust, setImgCacheBust] = useState(0)

  // Zoomed page image
  const [zoomedImg, setZoomedImg] = useState(null)

  const load = async () => {
    try {
      setLoading(true)
      const g = await api.getGroup(id)
      setGroup(g)
      setPageOrder(g.pages || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [id])
  useEffect(() => { if (addingEntity) entityNameRef.current?.focus() }, [addingEntity])

  const save = async (field, value) => {
    await api.updateGroup(id, { [field]: value })
    setGroup(prev => ({ ...prev, [field]: value }))
  }

  const removeEntity = async (entityId) => {
    try {
      await api.removeGroupEntity(id, entityId)
      setGroup(prev => ({ ...prev, entities: prev.entities.filter(e => e.id !== entityId) }))
    } catch (err) {
      alert(err.message)
    }
  }

  const addEntity = async () => {
    const name = newEntityName.trim()
    if (!name) return
    setEntitySaving(true)
    try {
      const res = await api.addGroupEntity(id, name, newEntityType, newEntityRole.trim() || undefined)
      const newEnt = { ...res.entity, role: res.role }
      setGroup(prev => ({ ...prev, entities: [...(prev.entities || []), newEnt] }))
      setNewEntityName('')
      setNewEntityRole('')
      setAddingEntity(false)
    } catch (err) {
      alert(err.message)
    } finally {
      setEntitySaving(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await api.deleteGroup(id)
      navigate('/gallery', { replace: true })
    } catch (err) {
      alert(err.message)
      setDeleting(false)
    }
  }

  const handleReExtract = async () => {
    if (!window.confirm(`Re-extract all ${pageOrder.length} pages? This will overwrite title, description, entities, transactions, and tags. Your annotation will be preserved.`)) return
    setReExtracting(true)
    setStatus({ message: `Re-extracting group #${id} (${pageOrder.length} pages)…`, busy: true })
    try {
      await api.reExtractGroup(id)
      await load()
      setStatus({ message: `Done: re-extracted group #${id}`, busy: false })
      setTimeout(() => setStatus({ message: '', busy: false }), 5000)
    } catch (err) {
      setStatus({ message: `Failed to re-extract: ${err.message}`, busy: false })
      setTimeout(() => setStatus({ message: '', busy: false }), 8000)
      alert(err.message)
    } finally {
      setReExtracting(false)
    }
  }

  // ── Drag-and-drop page reordering ─────────────────────────────────────────

  const onDragStart = (e, idx) => {
    setDragIdx(idx)
    e.dataTransfer.effectAllowed = 'move'
  }

  const onDragOver = (e, idx) => {
    e.preventDefault()
    if (dragIdx === null || dragIdx === idx) return
    const newOrder = [...pageOrder]
    const [moved]  = newOrder.splice(dragIdx, 1)
    newOrder.splice(idx, 0, moved)
    setPageOrder(newOrder)
    setDragIdx(idx)
  }

  const onDrop = async (e) => {
    e.preventDefault()
    setDragIdx(null)
    try {
      await api.reorderGroupPages(id, pageOrder.map(p => p.id))
    } catch (err) {
      alert(err.message)
      load() // revert
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (loading) return <div className="loading" style={{ paddingTop: '4rem' }}>Loading group…</div>
  if (error)   return <div style={{ padding: '2rem', color: 'var(--rust)' }}>Error: {error}</div>
  if (!group)  return null

  const metaRows = [
    { label: 'Date',       field: 'date_depicted',  value: group.date_depicted  },
    { label: 'Location',   field: 'location',        value: group.location       },
    { label: 'Medium',     field: 'medium',          value: group.medium,
      render: () => (
        <div>
          <select
            value={group.medium_category || ''}
            onChange={e => save('medium_category', e.target.value || null)}
            style={{ width: 'auto', textTransform: 'capitalize', fontWeight: 600, fontSize: '0.95em', padding: '0.2rem 0.4rem' }}
          >
            <option value="">— uncategorized —</option>
            {MEDIUM_CATEGORIES.map(c => (
              <option key={c} value={c} style={{ textTransform: 'capitalize' }}>{c}</option>
            ))}
          </select>
          <div style={{ fontStyle: group.medium ? 'italic' : 'normal', color: 'var(--text-muted)', fontSize: '0.9em', marginTop: '0.2rem' }}>
            <InlineEdit value={group.medium} onSave={v => save('medium', v)} placeholder="Add medium detail…" />
          </div>
        </div>
      )
    },
    { label: 'Dimensions', field: 'dimensions',      value: group.dimensions     },
    { label: 'Language',   field: 'language',        value: group.language       },
    { label: 'Source',     field: 'source_archive',  value: group.source_archive },
  ]

  const ENTITY_TYPES = ['person', 'object', 'institution', 'unknown']

  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ position: 'sticky', top: 0, zIndex: 10, background: 'var(--cream-light)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
          <button className="btn btn-ghost" onClick={() => navigate(-1)} style={{ padding: '0.25rem 0.6rem' }}>← Back</button>
          <span style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', padding: '0.15rem 0.5rem', background: 'var(--navy)', color: 'white', borderRadius: '3px' }}>
            ⊞ {pageOrder.length} pages
          </span>
          <h1 style={{ flex: 1, marginBottom: 0, fontSize: '1.3rem' }}>
            <InlineEdit
              value={group.title}
              onSave={v => save('title', v)}
              placeholder="Add a title…"
            />
          </h1>
          <EvidenceFlag
            docId={`grp_${group.id}`}
            initial={!!group.is_key_evidence}
            onToggle={(v) => save('is_key_evidence', v ? 1 : 0)}
          />
          <button className="btn btn-ghost" onClick={() => setShowTranscription(true)}>
            📄 Transcription{!group.transcription && ' +'}
          </button>
          <button
            className="btn btn-ghost"
            onClick={handleReExtract}
            disabled={reExtracting}
            style={{ fontSize: '0.85rem' }}
          >
            {reExtracting ? 'Re-extracting…' : '↺ Re-extract'}
          </button>
          <button
            onClick={() => setDeleteModalOpen(true)}
            style={{
              background: 'none', border: '1px solid var(--border)', borderRadius: '3px',
              padding: '0.25rem 0.6rem', cursor: 'pointer', fontSize: '0.85rem',
              color: 'var(--text-muted)', fontFamily: 'inherit', transition: 'all 0.15s',
            }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--rust)'; e.currentTarget.style.borderColor = 'var(--rust)'; e.currentTarget.style.background = '#fff1f0' }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.background = 'none' }}
          >
            ✕ Delete Group
          </button>
        </div>
      </div>

      {/* Page strip */}
      <div style={{ borderBottom: '1px solid var(--border)', background: 'var(--navy-deep)', padding: '0.75rem 1.5rem' }}>
        <div style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.5)', marginBottom: '0.5rem' }}>
          Pages — drag to reorder
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', overflowX: 'auto', paddingBottom: '0.25rem' }}>
          {pageOrder.map((page, idx) => (
            <div
              key={page.id}
              draggable
              onDragStart={e => onDragStart(e, idx)}
              onDragOver={e => onDragOver(e, idx)}
              onDrop={onDrop}
              onClick={() => setZoomedImg(api.getDocumentImageUrl(page.id))}
              style={{
                flexShrink: 0,
                width: '80px',
                cursor: 'grab',
                opacity: dragIdx === idx ? 0.5 : 1,
                transition: 'opacity 0.15s',
              }}
            >
              <div style={{ position: 'relative' }}>
                <img
                  src={`${api.getDocumentThumbnailUrl(page.id)}?v=${imgCacheBust}`}
                  alt={`Page ${idx + 1}`}
                  style={{
                    width: '80px', height: '60px', objectFit: 'cover',
                    borderRadius: '3px', border: '2px solid rgba(255,255,255,0.15)',
                    display: 'block',
                  }}
                />
                <span style={{
                  position: 'absolute', bottom: '2px', right: '2px',
                  background: 'rgba(0,0,0,0.65)', color: 'white',
                  fontSize: '0.65rem', padding: '1px 4px', borderRadius: '2px',
                }}>
                  {idx + 1}
                </span>
              </div>
              <div style={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.55)', marginTop: '0.2rem', textAlign: 'center', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                <Link to={`/documents/${page.id}`} style={{ color: 'rgba(255,255,255,0.55)', textDecoration: 'none' }} onClick={e => e.stopPropagation()}>
                  {page.filename}
                </Link>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Main two-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: 0, minHeight: 'calc(100vh - 200px)' }}>
        {/* Left: metadata */}
        <div style={{ padding: '1.5rem 2rem', borderRight: '1px solid var(--border)' }}>

          {/* Metadata table */}
          <table style={{ width: '100%', fontSize: '0.9rem', borderCollapse: 'collapse', marginBottom: '1.5rem' }}>
            <tbody>
              {metaRows.map(({ label, field, value, render }) => (
                <tr key={label}>
                  <td style={{ padding: '0.35rem 0', color: 'var(--text-muted)', fontWeight: 600, width: '120px', fontSize: '0.82rem', textTransform: 'uppercase', letterSpacing: '0.03em', verticalAlign: 'top', paddingTop: '0.45rem' }}>
                    {label}
                  </td>
                  <td style={{ padding: '0.35rem 0', color: 'var(--text-body)' }}>
                    {render
                      ? render()
                      : <InlineEdit value={value} onSave={v => save(field, v)} placeholder={`Add ${label.toLowerCase()}…`} />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <hr className="divider" />

          {/* Description */}
          <div style={{ marginBottom: '1.5rem' }}>
            <h3 style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>Description</h3>
            <InlineEdit
              value={group.description}
              onSave={v => save('description', v)}
              multiline
              placeholder="Click to add a description…"
              emptyLabel="No description — click to add one"
            />
          </div>

          {/* Transactions */}
          <TransactionEditor transactions={group.transactions || []} groupId={group.id} />

          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '1rem' }}>
            <div>Created: {new Date(group.created_at).toLocaleDateString()}</div>
            <div>Updated: {new Date(group.updated_at).toLocaleDateString()}</div>
          </div>
        </div>

        {/* Right: annotation / tags / entities */}
        <div style={{ padding: '1.5rem', background: 'var(--cream-light)', borderTop: '1px solid var(--border)' }}>
          <div style={{ marginBottom: '1.5rem' }}>
            <AnnotationPanel docId={`grp_${group.id}`} initialValue={group.annotation || ''} onSave={v => save('annotation', v)} />
          </div>

          <hr className="divider" style={{ margin: '1rem 0' }} />

          <div style={{ marginBottom: '1.5rem' }}>
            <TagManager docId={`grp_${group.id}`} initialTags={group.tags || []} isGroup groupId={group.id} />
          </div>

          <hr className="divider" style={{ margin: '1rem 0' }} />

          {/* Entities */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
              <h3 style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: 0 }}>
                Entities {group.entities?.length > 0 && `(${group.entities.length})`}
              </h3>
              <button
                onClick={() => setAddingEntity(v => !v)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem', color: 'var(--navy-light)', fontWeight: 600, padding: 0 }}
              >
                {addingEntity ? 'Cancel' : '+ Add'}
              </button>
            </div>

            {addingEntity && (
              <div style={{ background: 'var(--cream-card)', border: '1px solid var(--border)', borderRadius: '4px', padding: '0.75rem', marginBottom: '0.75rem' }}>
                <div style={{ marginBottom: '0.5rem' }}>
                  <EntityNameAutocomplete
                    inputRef={entityNameRef}
                    value={newEntityName}
                    onChange={setNewEntityName}
                    onPick={(ent) => {
                      setNewEntityName(ent.name)
                      if (ent.type) setNewEntityType(ent.type)
                    }}
                    onEnter={addEntity}
                    onEscape={() => setAddingEntity(false)}
                  />
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
                  <select
                    value={newEntityType}
                    onChange={e => setNewEntityType(e.target.value)}
                    style={{ flex: 1, fontSize: '0.85rem' }}
                  >
                    {ENTITY_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                  </select>
                  <input
                    type="text"
                    value={newEntityRole}
                    onChange={e => setNewEntityRole(e.target.value)}
                    placeholder="Role (optional)…"
                    style={{ flex: 1, fontSize: '0.85rem' }}
                  />
                </div>
                <button
                  className="btn btn-primary"
                  onClick={addEntity}
                  disabled={!newEntityName.trim() || entitySaving}
                  style={{ fontSize: '0.82rem', padding: '0.3rem 0.75rem' }}
                >
                  {entitySaving ? 'Saving…' : 'Add Entity'}
                </button>
              </div>
            )}

            {ENTITY_TYPES.map(type => {
              const grp = (group.entities || []).filter(e => e.type === type)
              if (!grp.length) return null
              return (
                <div key={type} style={{ marginBottom: '0.75rem' }}>
                  <div style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-light)', marginBottom: '0.3rem' }}>
                    {type}s
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                    {grp.map(e => (
                      <span key={`${e.id}-${e.role}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
                        <EntityTag entity={e} />
                        <button
                          onClick={() => removeEntity(e.id)}
                          title={`Remove ${e.name}`}
                          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-light)', fontSize: '0.75rem', lineHeight: 1, padding: '0 2px', marginBottom: '0.3rem' }}
                          onMouseEnter={e => e.currentTarget.style.color = 'var(--rust)'}
                          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-light)'}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
              )
            })}

            {(!group.entities || group.entities.length === 0) && !addingEntity && (
              <div style={{ fontSize: '0.85rem', color: 'var(--text-light)', fontStyle: 'italic' }}>No entities — click + Add to add one</div>
            )}
          </div>
        </div>
      </div>

      {/* Transcription modal */}
      {showTranscription && (
        <div className="modal-overlay" onClick={() => setShowTranscription(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ width: '90vw', maxWidth: '1100px', maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
              <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Transcription</h2>
              <button onClick={() => setShowTranscription(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.2rem', color: 'var(--text-muted)' }}>✕</button>
            </div>
            <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
              <TranscriptionEditor
                value={group.transcription}
                onSave={v => save('transcription', v)}
                emptyLabel="No transcription — click Edit to add one."
              />
            </div>
          </div>
        </div>
      )}

      {/* Page zoom modal */}
      {zoomedImg && (
        <div className="modal-overlay" onClick={() => setZoomedImg(null)} style={{ cursor: 'zoom-out' }}>
          <img
            src={zoomedImg}
            alt="Page"
            style={{ maxWidth: '95vw', maxHeight: '95vh', objectFit: 'contain', border: '2px solid var(--border)' }}
            onClick={e => e.stopPropagation()}
          />
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteModalOpen && (
        <div className="modal-overlay" onClick={() => !deleting && setDeleteModalOpen(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: '460px' }}>
            <h2 style={{ marginBottom: '0.75rem', fontSize: '1.15rem', color: 'var(--rust)' }}>Delete Group?</h2>
            <p style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-body)', marginBottom: '1rem' }}>
              This will delete the group record and all its extracted metadata (entities, transactions, tags, annotation).
            </p>
            <p style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-body)', marginBottom: '1.5rem' }}>
              The <strong>{pageOrder.length} individual page documents</strong> and their photo files will <strong>not</strong> be deleted — they will return to the gallery as standalone documents.
            </p>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setDeleteModalOpen(false)} disabled={deleting}>Cancel</button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                style={{ background: 'var(--rust)', color: 'white', border: 'none', borderRadius: '3px', padding: '0.5rem 1.1rem', cursor: 'pointer', fontFamily: 'inherit', fontSize: '0.9rem', fontWeight: 600 }}
              >
                {deleting ? 'Deleting…' : 'Delete Group'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
