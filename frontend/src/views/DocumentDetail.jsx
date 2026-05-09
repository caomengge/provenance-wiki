import React, { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import api from '../api/client'
import EntityTag from '../components/EntityTag'
import AnnotationPanel from '../components/AnnotationPanel'
import EvidenceFlag from '../components/EvidenceFlag'
import TagManager from '../components/TagManager'
import DocumentLinkModal from '../components/DocumentLinkModal'
import InlineEdit from '../components/InlineEdit'
import TransactionEditor from '../components/TransactionEditor'

export default function DocumentDetail() {
  const { id }       = useParams()
  const navigate     = useNavigate()
  const [doc, setDoc]           = useState(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState('')
  const [showLink, setShowLink] = useState(false)
  const [imgZoomed, setImgZoomed] = useState(false)
  const [showTranscription, setShowTranscription] = useState(false)
  const [rotating, setRotating] = useState(false)
  const [imgCacheBust, setImgCacheBust] = useState(0)
  const [wipeModalOpen, setWipeModalOpen] = useState(false)
  const [wiping, setWiping] = useState(false)
  const [archives, setArchives] = useState([])

  // Trash
  const [trashModalOpen, setTrashModalOpen] = useState(false)
  const [trashSaving,    setTrashSaving]    = useState(false)

  // Delete
  const [deleteModalOpen, setDeleteModalOpen] = useState(false)
  const [deleteDeleting,  setDeleteDeleting]  = useState(false)

  // Entity management
  const [addingEntity,   setAddingEntity]   = useState(false)
  const [newEntityName,  setNewEntityName]  = useState('')
  const [newEntityType,  setNewEntityType]  = useState('person')
  const [newEntityRole,  setNewEntityRole]  = useState('')
  const [entitySaving,   setEntitySaving]   = useState(false)
  const entityNameRef = useRef(null)

  const load = async () => {
    try {
      setLoading(true)
      const d = await api.getDocument(id)
      setDoc(d)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [id])
  useEffect(() => { api.getArchives().then(r => setArchives(r.archives || [])).catch(() => {}) }, [])

  useEffect(() => {
    if (addingEntity) entityNameRef.current?.focus()
  }, [addingEntity])

  // ── Delete handler ──────────────────────────────────────────────────────────

  const handleDelete = async () => {
    setDeleteDeleting(true)
    try {
      await api.deleteDocument(id)
      navigate('/', { replace: true })
    } catch (err) {
      alert(err.message)
      setDeleteDeleting(false)
    }
  }

  // ── Trash helpers ───────────────────────────────────────────────────────────

  const toggleTrash = async () => {
    setTrashSaving(true)
    try {
      const newValue = doc.is_trashed ? 0 : 1
      await api.updateDocument(id, { is_trashed: newValue })
      setDoc(prev => ({ ...prev, is_trashed: newValue }))
      setTrashModalOpen(false)
    } catch (err) {
      alert(err.message)
    } finally {
      setTrashSaving(false)
    }
  }

  // ── Save helpers ────────────────────────────────────────────────────────────

  const save = async (field, value) => {
    await api.updateDocument(id, { [field]: value })
    setDoc(prev => ({ ...prev, [field]: value }))
  }

  const handleRotate = async (direction) => {
    setRotating(true)
    try {
      await api.rotateDocument(id, direction)
      setImgCacheBust(v => v + 1)
    } catch (err) {
      alert(err.message)
    } finally {
      setRotating(false)
    }
  }

  const handleWipe = async () => {
    setWiping(true)
    try {
      await api.wipeDocument(id)
      setWipeModalOpen(false)
      load()
    } catch (err) {
      alert(err.message)
    } finally {
      setWiping(false)
    }
  }

  const handleDeleteLink = async (linkId) => {
    if (!window.confirm('Remove this document link?')) return
    try {
      await api.deleteLink(id, linkId)
      setDoc(prev => ({ ...prev, links: prev.links.filter(l => l.id !== linkId) }))
    } catch (err) {
      alert(err.message)
    }
  }

  // ── Entity management ───────────────────────────────────────────────────────

  const removeEntity = async (entityId) => {
    try {
      await api.removeDocumentEntity(id, entityId)
      setDoc(prev => ({ ...prev, entities: prev.entities.filter(e => e.id !== entityId) }))
    } catch (err) {
      alert(err.message)
    }
  }

  const addEntity = async () => {
    const name = newEntityName.trim()
    if (!name) return
    setEntitySaving(true)
    try {
      const res = await api.addDocumentEntity(id, name, newEntityType, newEntityRole.trim() || undefined)
      const newEnt = { ...res.entity, role: res.role }
      setDoc(prev => ({
        ...prev,
        entities: [...(prev.entities || []), newEnt],
      }))
      setNewEntityName('')
      setNewEntityRole('')
      setAddingEntity(false)
    } catch (err) {
      alert(err.message)
    } finally {
      setEntitySaving(false)
    }
  }

  // ── Render guards ───────────────────────────────────────────────────────────

  if (loading) return <div className="loading" style={{ paddingTop: '4rem' }}>Loading document…</div>
  if (error)   return <div style={{ padding: '2rem', color: 'var(--rust)' }}>Error: {error}</div>
  if (!doc)    return null

  const imgUrl = api.getDocumentImageUrl(doc.id)

  // ── Metadata rows config ────────────────────────────────────────────────────

  const metaRows = [
    { label: 'File',       value: doc.filename,        readOnly: true },
    { label: 'Source',     field: 'source_archive',    value: doc.source_archive,  suggestions: archives },
    { label: 'Date',       field: 'date_depicted',     value: doc.date_depicted   },
    { label: 'Location',   field: 'location',          value: doc.location        },
    { label: 'Medium',     field: 'medium',            value: doc.medium          },
    { label: 'Dimensions', field: 'dimensions',        value: doc.dimensions      },
    { label: 'Language',   field: 'language',          value: doc.language        },
  ]

  // ── Entity type styles ──────────────────────────────────────────────────────

  const ENTITY_TYPES = ['person', 'object', 'institution', 'unknown']

  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ position: 'sticky', top: 0, zIndex: 10, background: 'var(--cream-light)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
          <button className="btn btn-ghost" onClick={() => navigate(-1)} style={{ padding: '0.25rem 0.6rem' }}>← Back</button>
          <h1 style={{ flex: 1, marginBottom: 0, fontSize: '1.3rem' }}>
            <InlineEdit
              value={doc.title || doc.filename}
              onSave={v => save('title', v)}
              placeholder="Add a title…"
            />
          </h1>
          <EvidenceFlag
            docId={doc.id}
            initial={!!doc.is_key_evidence}
            onToggle={async (v) => {
              await api.updateDocument(doc.id, { is_key_evidence: v ? 1 : 0 })
              setDoc(prev => ({ ...prev, is_key_evidence: v ? 1 : 0 }))
            }}
          />
          <button className="btn btn-ghost" onClick={() => setShowLink(true)}>⛓ Link Document</button>
          {doc.raw_claude_response && (
            <button className="btn btn-ghost" onClick={() => setShowTranscription(true)}>📄 Transcription</button>
          )}
          <a
            href="#"
            className="btn btn-ghost"
            onClick={(e) => { e.preventDefault(); window.open(`/api/export/selection`, '_blank') }}
          >
            ↓ PDF
          </a>
          <button
            onClick={() => doc.is_trashed ? toggleTrash() : setTrashModalOpen(true)}
            style={{
              background: 'none',
              border: '1px solid var(--border)',
              borderRadius: '3px',
              padding: '0.25rem 0.6rem',
              cursor: 'pointer',
              fontSize: '0.85rem',
              color: doc.is_trashed ? 'var(--navy-light)' : 'var(--text-muted)',
              fontFamily: 'inherit',
              transition: 'all 0.15s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.color = doc.is_trashed ? 'var(--navy)' : 'var(--rust)'
              e.currentTarget.style.borderColor = doc.is_trashed ? 'var(--navy)' : 'var(--rust)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.color = doc.is_trashed ? 'var(--navy-light)' : 'var(--text-muted)'
              e.currentTarget.style.borderColor = 'var(--border)'
            }}
            title={doc.is_trashed ? 'Restore from trash' : 'Move to trash'}
          >
            {doc.is_trashed ? '↩ Restore' : '🗑 Trash'}
          </button>
          <button
            onClick={() => setWipeModalOpen(true)}
            style={{
              background: 'none',
              border: '1px solid var(--border)',
              borderRadius: '3px',
              padding: '0.25rem 0.6rem',
              cursor: 'pointer',
              fontSize: '0.85rem',
              color: 'var(--text-muted)',
              fontFamily: 'inherit',
              transition: 'all 0.15s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.color = 'var(--rust)'
              e.currentTarget.style.borderColor = 'var(--rust)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.color = 'var(--text-muted)'
              e.currentTarget.style.borderColor = 'var(--border)'
            }}
            title="Clear extracted metadata and re-queue for extraction"
          >
            ⟳ Wipe Metadata
          </button>
          <button
            onClick={() => setDeleteModalOpen(true)}
            style={{
              background: 'none',
              border: '1px solid var(--border)',
              borderRadius: '3px',
              padding: '0.25rem 0.6rem',
              cursor: 'pointer',
              fontSize: '0.85rem',
              color: 'var(--text-muted)',
              fontFamily: 'inherit',
              transition: 'all 0.15s',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.color = 'var(--rust)'
              e.currentTarget.style.borderColor = 'var(--rust)'
              e.currentTarget.style.background = '#fff1f0'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.color = 'var(--text-muted)'
              e.currentTarget.style.borderColor = 'var(--border)'
              e.currentTarget.style.background = 'none'
            }}
            title="Permanently delete this document and photo"
          >
            ✕ Delete
          </button>
        </div>
      </div>

      {/* Group banner */}
      {doc.group_id && (
        <div style={{
          background: '#eef4fb',
          borderBottom: '1px solid #b8d0eb',
          padding: '0.5rem 2rem',
          fontSize: '0.85rem',
          color: '#1a3a5c',
        }}>
          This is page {doc.page_number} of a{' '}
          <Link to={`/groups/${doc.group_id}`} style={{ color: 'var(--navy-light)', fontWeight: 600 }}>
            multi-page document group
          </Link>.
          {' '}Canonical metadata and transcription are on the group record.
        </div>
      )}

      {/* Trashed banner */}
      {!!doc.is_trashed && (
        <div style={{
          background: '#fef3c7',
          borderBottom: '1px solid #d97706',
          padding: '0.6rem 2rem',
          fontSize: '0.88rem',
          color: '#78350f',
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
        }}>
          <span>⚠️ This document is trashed — it is hidden from search, timeline, network graph, and the research assistant.</span>
          <button
            onClick={toggleTrash}
            disabled={trashSaving}
            style={{ background: 'none', border: '1px solid #d97706', borderRadius: '3px', padding: '0.15rem 0.5rem', cursor: 'pointer', fontSize: '0.82rem', color: '#78350f', fontFamily: 'inherit' }}
          >
            {trashSaving ? 'Restoring…' : 'Restore'}
          </button>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: 0, minHeight: 'calc(100vh - 100px)' }}>
        {/* Left: image + metadata */}
        <div style={{ padding: '1.5rem 2rem', borderRight: '1px solid var(--border)' }}>

          {/* Image */}
          <div style={{ position: 'relative', marginBottom: '1.5rem' }}>
            <div style={{ cursor: 'zoom-in' }} onClick={() => setImgZoomed(true)}>
              <img
                src={`${imgUrl}?v=${imgCacheBust}`}
                alt={doc.title || doc.filename}
                style={{
                  maxWidth: '100%',
                  maxHeight: '60vh',
                  display: 'block',
                  objectFit: 'contain',
                  border: '1px solid var(--border)',
                  borderRadius: '3px',
                  background: 'var(--navy-deep)',
                  opacity: rotating ? 0.5 : 1,
                  transition: 'opacity 0.2s',
                }}
              />
              <div style={{ position: 'absolute', bottom: '0.5rem', right: '0.5rem', background: 'rgba(0,0,0,0.5)', color: 'white', fontSize: '0.75rem', padding: '2px 6px', borderRadius: '2px' }}>
                Click to zoom
              </div>
            </div>
            {/* Rotate buttons */}
            <div style={{ position: 'absolute', top: '0.5rem', right: '0.5rem', display: 'flex', gap: '0.25rem' }}>
              {[['↺', 'ccw', 'Rotate counter-clockwise'], ['↻', 'cw', 'Rotate clockwise']].map(([icon, dir, label]) => (
                <button
                  key={dir}
                  onClick={() => handleRotate(dir)}
                  disabled={rotating}
                  title={label}
                  style={{
                    background: 'rgba(0,0,0,0.55)',
                    color: 'white',
                    border: 'none',
                    borderRadius: '3px',
                    padding: '3px 8px',
                    cursor: rotating ? 'not-allowed' : 'pointer',
                    fontSize: '1rem',
                    lineHeight: 1,
                    opacity: rotating ? 0.5 : 1,
                  }}
                >
                  {icon}
                </button>
              ))}
            </div>
          </div>

          {/* Metadata table */}
          <table style={{ width: '100%', fontSize: '0.9rem', borderCollapse: 'collapse' }}>
            <tbody>
              {metaRows.map(({ label, field, value, readOnly, suggestions }) => (
                <tr key={label}>
                  <td style={{ padding: '0.35rem 0', color: 'var(--text-muted)', fontWeight: 600, width: '120px', fontSize: '0.82rem', textTransform: 'uppercase', letterSpacing: '0.03em', verticalAlign: 'top', paddingTop: '0.45rem' }}>
                    {label}
                  </td>
                  <td style={{ padding: '0.35rem 0', color: 'var(--text-body)' }}>
                    {readOnly
                      ? value
                      : <InlineEdit value={value} onSave={v => save(field, v)} placeholder={`Add ${label.toLowerCase()}…`} suggestions={suggestions} />
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <hr className="divider" />

          {/* Description */}
          <div style={{ marginBottom: '1.5rem' }}>
            <h3 style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
              Description
            </h3>
            <InlineEdit
              value={doc.description}
              onSave={v => save('description', v)}
              multiline
              placeholder="Click to add a description…"
              emptyLabel="No description — click to add one"
            />
          </div>

          {/* Transactions */}
          <TransactionEditor transactions={doc.transactions || []} docId={id} />

          {/* Linked documents */}
          {doc.links?.length > 0 && (
            <div>
              <h3 style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                Linked Documents
              </h3>
              {doc.links.map(link => {
                const other = link.source_id == id ? { id: link.target_id, title: link.target_title } : { id: link.source_id, title: link.source_title }
                return (
                  <div key={link.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem', fontSize: '0.88rem' }}>
                    <Link to={`/documents/${other.id}`} style={{ color: 'var(--navy-light)', fontWeight: 500 }}>
                      {other.title || `Document #${other.id}`}
                    </Link>
                    <span style={{ color: 'var(--text-muted)' }}>— {link.relationship_type}</span>
                    <button
                      onClick={() => handleDeleteLink(link.id)}
                      style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-light)', fontSize: '0.85rem' }}
                    >Remove</button>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Right: annotation / tags / entities */}
        <div style={{ padding: '1.5rem', background: 'var(--cream-light)', borderTop: '1px solid var(--border)' }}>
          <div style={{ marginBottom: '1.5rem' }}>
            <AnnotationPanel docId={doc.id} initialValue={doc.annotation || ''} />
          </div>

          <hr className="divider" style={{ margin: '1rem 0' }} />

          <div style={{ marginBottom: '1.5rem' }}>
            <TagManager docId={doc.id} initialTags={doc.tags || []} />
          </div>

          <hr className="divider" style={{ margin: '1rem 0' }} />

          {/* Entities */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
              <h3 style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: 0 }}>
                Entities {doc.entities?.length > 0 && `(${doc.entities.length})`}
              </h3>
              <button
                onClick={() => setAddingEntity(v => !v)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem', color: 'var(--navy-light)', fontWeight: 600, padding: 0 }}
              >
                {addingEntity ? 'Cancel' : '+ Add'}
              </button>
            </div>

            {/* Add entity form */}
            {addingEntity && (
              <div style={{ background: 'var(--cream-card)', border: '1px solid var(--border)', borderRadius: '4px', padding: '0.75rem', marginBottom: '0.75rem' }}>
                <input
                  ref={entityNameRef}
                  type="text"
                  value={newEntityName}
                  onChange={e => setNewEntityName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') addEntity(); if (e.key === 'Escape') setAddingEntity(false) }}
                  placeholder="Entity name…"
                  style={{ width: '100%', fontSize: '0.88rem', marginBottom: '0.5rem', boxSizing: 'border-box' }}
                />
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

            {/* Entity list grouped by type */}
            {ENTITY_TYPES.map(type => {
              const group = (doc.entities || []).filter(e => e.type === type)
              if (!group.length) return null
              return (
                <div key={type} style={{ marginBottom: '0.75rem' }}>
                  <div style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-light)', marginBottom: '0.3rem' }}>
                    {type}s
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                    {group.map(e => (
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

            {(!doc.entities || doc.entities.length === 0) && !addingEntity && (
              <div style={{ fontSize: '0.85rem', color: 'var(--text-light)', fontStyle: 'italic' }}>No entities — click + Add to add one</div>
            )}
          </div>

          <hr className="divider" style={{ margin: '1rem 0' }} />

          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            <div>Added: {new Date(doc.created_at).toLocaleDateString()}</div>
            <div>Updated: {new Date(doc.updated_at).toLocaleDateString()}</div>
            <div style={{ marginTop: '0.4rem' }}>
              <button
                className="btn btn-ghost"
                style={{ fontSize: '0.8rem', padding: '0.25rem 0.6rem' }}
                onClick={() => navigate(`/network?doc_id=${doc.id}`)}
              >
                View in Network →
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Wipe metadata confirmation modal */}
      {wipeModalOpen && (
        <div className="modal-overlay" onClick={() => !wiping && setWipeModalOpen(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: '480px' }}>
            <h2 style={{ marginBottom: '0.75rem', fontSize: '1.15rem', color: 'var(--rust)' }}>Wipe Extracted Metadata?</h2>
            <p style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-body)', marginBottom: '0.75rem' }}>
              This will permanently clear all Claude-extracted data from this document:
            </p>
            <ul style={{ fontSize: '0.9rem', lineHeight: 1.8, color: 'var(--rust)', paddingLeft: '1.25rem', marginBottom: '1rem' }}>
              <li>Title, date, location, medium, dimensions, language</li>
              <li>Description and transcription</li>
              <li>All extracted entities and transactions</li>
              <li>Key evidence flag</li>
            </ul>
            <p style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-body)', marginBottom: '0.75rem' }}>
              The following will <strong>not</strong> be affected:
            </p>
            <ul style={{ fontSize: '0.9rem', lineHeight: 1.8, color: 'var(--text-body)', paddingLeft: '1.25rem', marginBottom: '1rem' }}>
              <li>The original photo file</li>
              <li>Your researcher annotation</li>
              <li>Tags and document links</li>
            </ul>
            <p style={{ fontSize: '0.9rem', lineHeight: 1.7, color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
              The document will be re-queued for extraction the next time you run Ingest.
            </p>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setWipeModalOpen(false)} disabled={wiping}>Cancel</button>
              <button
                onClick={handleWipe}
                disabled={wiping}
                style={{ background: 'var(--rust)', color: 'white', border: 'none', borderRadius: '3px', padding: '0.5rem 1.1rem', cursor: 'pointer', fontFamily: 'inherit', fontSize: '0.9rem', fontWeight: 600, opacity: wiping ? 0.7 : 1 }}
              >
                {wiping ? 'Wiping…' : 'Wipe Metadata'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteModalOpen && (
        <div className="modal-overlay" onClick={() => !deleteDeleting && setDeleteModalOpen(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: '460px' }}>
            <h2 style={{ marginBottom: '0.75rem', fontSize: '1.15rem', color: 'var(--rust)' }}>Permanently Delete?</h2>
            <p style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-body)', marginBottom: '0.75rem' }}>
              This will <strong>permanently</strong> delete:
            </p>
            <ul style={{ fontSize: '0.9rem', lineHeight: 1.8, color: 'var(--text-body)', paddingLeft: '1.25rem', marginBottom: '1rem' }}>
              <li>The photo file from the <code>photos/</code> folder</li>
              <li>All extracted metadata (title, date, location, description…)</li>
              <li>All annotations, tags, entity links, and transactions</li>
              <li>All document links to other records</li>
            </ul>
            <p style={{ fontSize: '0.9rem', lineHeight: 1.7, color: 'var(--rust)', fontWeight: 600, marginBottom: '1.5rem' }}>
              This cannot be undone. If you only want to hide the document, use Trash instead.
            </p>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setDeleteModalOpen(false)} disabled={deleteDeleting}>Cancel</button>
              <button
                onClick={handleDelete}
                disabled={deleteDeleting}
                style={{ background: 'var(--rust)', color: 'white', border: 'none', borderRadius: '3px', padding: '0.5rem 1.1rem', cursor: 'pointer', fontFamily: 'inherit', fontSize: '0.9rem', fontWeight: 600, opacity: deleteDeleting ? 0.7 : 1 }}
              >
                {deleteDeleting ? 'Deleting…' : 'Delete Permanently'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Trash confirmation modal */}
      {trashModalOpen && (
        <div className="modal-overlay" onClick={() => setTrashModalOpen(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: '460px' }}>
            <h2 style={{ marginBottom: '0.75rem', fontSize: '1.15rem', color: 'var(--navy)' }}>Move to Trash?</h2>
            <p style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-body)', marginBottom: '0.75rem' }}>
              This document's full record — all metadata, annotations, entities, tags, and links — will be <strong>preserved</strong>.
            </p>
            <p style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-body)', marginBottom: '1.25rem' }}>
              However, it will be <strong>hidden</strong> from:
            </p>
            <ul style={{ fontSize: '0.9rem', lineHeight: 1.8, color: 'var(--text-body)', paddingLeft: '1.25rem', marginBottom: '1.25rem' }}>
              <li>Search results</li>
              <li>Provenance timeline</li>
              <li>Network graph</li>
              <li>AI research assistant (Q&amp;A)</li>
            </ul>
            <p style={{ fontSize: '0.88rem', color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
              You can restore it at any time from this document's page.
            </p>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setTrashModalOpen(false)}>Cancel</button>
              <button
                onClick={toggleTrash}
                disabled={trashSaving}
                style={{ background: 'var(--rust)', color: 'white', border: 'none', borderRadius: '3px', padding: '0.5rem 1.1rem', cursor: 'pointer', fontFamily: 'inherit', fontSize: '0.9rem', fontWeight: 600 }}
              >
                {trashSaving ? 'Moving…' : 'Move to Trash'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Zoom modal */}
      {imgZoomed && (
        <div className="modal-overlay" onClick={() => setImgZoomed(false)} style={{ cursor: 'zoom-out' }}>
          <img
            src={imgUrl}
            alt={doc.title}
            style={{ maxWidth: '95vw', maxHeight: '95vh', objectFit: 'contain', border: '2px solid var(--border)' }}
            onClick={e => e.stopPropagation()}
          />
        </div>
      )}

      {/* Transcription modal */}
      {showTranscription && (
        <div className="modal-overlay" onClick={() => setShowTranscription(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: '680px', maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
              <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Transcription</h2>
              <button onClick={() => setShowTranscription(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.2rem', color: 'var(--text-muted)' }}>✕</button>
            </div>
            <div style={{ overflowY: 'auto', flex: 1 }}>
              {doc.transcription
                ? <pre style={{ margin: 0, fontFamily: 'inherit', fontSize: '0.92rem', lineHeight: 1.8, color: 'var(--text-body)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{doc.transcription}</pre>
                : <p style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>No transcription available. Re-ingest this document to generate one.</p>
              }
            </div>
          </div>
        </div>
      )}

      {/* Link modal */}
      {showLink && (
        <DocumentLinkModal
          docId={doc.id}
          onClose={() => setShowLink(false)}
          onLinked={(link) => setDoc(prev => ({ ...prev, links: [...(prev.links || []), { ...link, id: Date.now() }] }))}
        />
      )}
    </div>
  )
}
