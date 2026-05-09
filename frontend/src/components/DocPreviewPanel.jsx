import React, { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import api from '../api/client'
import TagManager from './TagManager'
import EntityTag from './EntityTag'
import InlineEdit from './InlineEdit'

const ENTITY_TYPES = ['person', 'object', 'institution', 'unknown']

export default function DocPreviewPanel({ docId, onClose }) {
  const [doc, setDoc]           = useState(null)
  const [loading, setLoading]   = useState(true)
  const [archives, setArchives] = useState([])

  // Entity management
  const [addingEntity,  setAddingEntity]  = useState(false)
  const [newEntityName, setNewEntityName] = useState('')
  const [newEntityType, setNewEntityType] = useState('person')
  const [newEntityRole, setNewEntityRole] = useState('')
  const [entitySaving,  setEntitySaving]  = useState(false)
  const entityNameRef = useRef(null)

  useEffect(() => {
    api.getArchives().then(r => setArchives(r.archives || [])).catch(() => {})
  }, [])

  useEffect(() => {
    setDoc(null)
    setLoading(true)
    setAddingEntity(false)
    setNewEntityName('')
    setNewEntityRole('')
    api.getDocument(docId)
      .then(setDoc)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [docId])

  useEffect(() => {
    if (addingEntity) entityNameRef.current?.focus()
  }, [addingEntity])

  const saveField = async (field, value) => {
    await api.updateDocument(docId, { [field]: value })
    setDoc(prev => ({ ...prev, [field]: value }))
  }

  const removeEntity = async (entityId) => {
    try {
      await api.removeDocumentEntity(docId, entityId)
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
      const res = await api.addDocumentEntity(docId, name, newEntityType, newEntityRole.trim() || undefined)
      const newEnt = { ...res.entity, role: res.role }
      setDoc(prev => ({ ...prev, entities: [...(prev.entities || []), newEnt] }))
      setNewEntityName('')
      setNewEntityRole('')
      setAddingEntity(false)
    } catch (err) {
      alert(err.message)
    } finally {
      setEntitySaving(false)
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(15,25,35,0.35)',
          zIndex: 200,
        }}
      />

      {/* Slide-in panel */}
      <div style={{
        position:      'fixed',
        top:           0,
        right:         0,
        bottom:        0,
        width:         '460px',
        background:    'var(--cream-card)',
        borderLeft:    '1px solid var(--border)',
        boxShadow:     '-4px 0 24px var(--shadow-deep)',
        zIndex:        201,
        display:       'flex',
        flexDirection: 'column',
        overflowY:     'auto',
      }}>

        {/* ── Header ─────────────────────────────────────────────────────────── */}
        <div style={{
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'space-between',
          padding:        '0.9rem 1.25rem',
          borderBottom:   '1px solid var(--border)',
          background:     'var(--cream-light)',
          position:       'sticky',
          top:            0,
          zIndex:         1,
          gap:            '0.75rem',
        }}>
          <span style={{
            fontWeight:   700,
            fontSize:     '0.9rem',
            color:        'var(--navy)',
            overflow:     'hidden',
            textOverflow: 'ellipsis',
            whiteSpace:   'nowrap',
            flex:         1,
          }}>
            {loading ? 'Loading…' : doc?.title || doc?.filename || `Document #${docId}`}
          </span>
          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', flexShrink: 0 }}>
            {doc && (
              <Link
                to={`/documents/${docId}`}
                style={{ fontSize: '0.82rem', color: 'var(--navy-light)', textDecoration: 'none', fontWeight: 500, whiteSpace: 'nowrap' }}
              >
                Open full page →
              </Link>
            )}
            <button
              onClick={onClose}
              aria-label="Close panel"
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.3rem', color: 'var(--text-muted)', lineHeight: 1, padding: '0 2px' }}
            >×</button>
          </div>
        </div>

        {/* ── Body ───────────────────────────────────────────────────────────── */}
        <div style={{ padding: '1.25rem', flex: 1 }}>
          {loading && (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>Loading document…</div>
          )}

          {!loading && doc && (
            <>
              {/* Image */}
              <img
                src={api.getDocumentThumbnailUrl(docId)}
                alt={doc.title || doc.filename}
                style={{
                  width:        '100%',
                  maxHeight:    '260px',
                  objectFit:    'contain',
                  border:       '1px solid var(--border)',
                  borderRadius: '3px',
                  background:   'var(--navy-deep)',
                  marginBottom: '1rem',
                  display:      'block',
                }}
              />

              {/* Metadata table */}
              <table style={{ width: '100%', fontSize: '0.88rem', borderCollapse: 'collapse', marginBottom: '1rem' }}>
                <tbody>
                  {/* Editable Source row — always shown so it's easy to set */}
                  <tr>
                    <td style={{ padding: '0.3rem 0', color: 'var(--text-muted)', fontWeight: 600, width: '90px', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.03em', verticalAlign: 'top', paddingTop: '0.45rem' }}>Source</td>
                    <td style={{ padding: '0.3rem 0', color: 'var(--text-body)' }}>
                      <InlineEdit
                        value={doc.source_archive}
                        onSave={v => saveField('source_archive', v)}
                        placeholder="Add archive / institution…"
                        suggestions={archives}
                      />
                    </td>
                  </tr>
                  {[
                    ['Date',       doc.date_depicted || (doc.date_range_start && `${doc.date_range_start} – ${doc.date_range_end || '?'}`)],
                    ['Location',   doc.location],
                    ['Medium',     doc.medium],
                    ['Dimensions', doc.dimensions],
                  ].filter(([, v]) => v).map(([k, v]) => (
                    <tr key={k}>
                      <td style={{ padding: '0.3rem 0', color: 'var(--text-muted)', fontWeight: 600, width: '90px', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{k}</td>
                      <td style={{ padding: '0.3rem 0', color: 'var(--text-body)' }}>{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Description */}
              {doc.description && (
                <p style={{ fontSize: '0.88rem', lineHeight: 1.7, color: 'var(--text-body)', margin: '0 0 1.25rem' }}>
                  {doc.description}
                </p>
              )}

              <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '1rem 0' }} />

              {/* ── Tags ────────────────────────────────────────────────────── */}
              <div style={{ marginBottom: '0.25rem' }}>
                <TagManager docId={doc.id} initialTags={doc.tags || []} />
              </div>

              <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '1rem 0' }} />

              {/* ── Entities ────────────────────────────────────────────────── */}
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

                {/* Add entity inline form */}
                {addingEntity && (
                  <div style={{
                    background:   'var(--cream-bg)',
                    border:       '1px solid var(--border)',
                    borderRadius: '4px',
                    padding:      '0.75rem',
                    marginBottom: '0.75rem',
                  }}>
                    <input
                      ref={entityNameRef}
                      type="text"
                      value={newEntityName}
                      onChange={e => setNewEntityName(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter')  addEntity()
                        if (e.key === 'Escape') setAddingEntity(false)
                      }}
                      placeholder="Entity name…"
                      style={{ width: '100%', fontSize: '0.88rem', marginBottom: '0.5rem', boxSizing: 'border-box' }}
                    />
                    <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
                      <select
                        value={newEntityType}
                        onChange={e => setNewEntityType(e.target.value)}
                        style={{ flex: 1, fontSize: '0.85rem' }}
                      >
                        {ENTITY_TYPES.map(t => (
                          <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                        ))}
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
                      <div style={{
                        fontSize:      '0.72rem',
                        fontWeight:    700,
                        textTransform: 'uppercase',
                        letterSpacing: '0.06em',
                        color:         'var(--text-light)',
                        marginBottom:  '0.3rem',
                      }}>
                        {type}s
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                        {group.map(e => (
                          <span key={`${e.id}-${e.role}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '2px' }}>
                            <EntityTag entity={e} />
                            <button
                              onClick={() => removeEntity(e.id)}
                              title={`Remove ${e.name} from this document`}
                              style={{
                                background: 'none',
                                border:     'none',
                                cursor:     'pointer',
                                color:      'var(--text-light)',
                                fontSize:   '0.8rem',
                                lineHeight: 1,
                                padding:    '0 2px',
                                marginBottom: '0.3rem',
                              }}
                              onMouseEnter={ev => ev.currentTarget.style.color = 'var(--rust)'}
                              onMouseLeave={ev => ev.currentTarget.style.color = 'var(--text-light)'}
                            >×</button>
                          </span>
                        ))}
                      </div>
                    </div>
                  )
                })}

                {(!doc.entities || doc.entities.length === 0) && !addingEntity && (
                  <div style={{ fontSize: '0.85rem', color: 'var(--text-light)', fontStyle: 'italic' }}>
                    No entities — click + Add to link one
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  )
}
