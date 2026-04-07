import React, { useState, useEffect, useCallback } from 'react'
import api from '../api/client'
import DocPreviewPanel from '../components/DocPreviewPanel'

export default function Trash() {
  const [docs,           setDocs]           = useState([])
  const [total,          setTotal]          = useState(0)
  const [loading,        setLoading]        = useState(true)
  const [previewDocId,   setPreviewDocId]   = useState(null)
  const [confirmDeleteId,setConfirmDeleteId]= useState(null)
  const [actionLoading,  setActionLoading]  = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.getDocuments({ show_trashed: 'true', per_page: 200, sort: 'updated_at', order: 'desc' })
      setDocs(res.documents || [])
      setTotal(res.total || 0)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const restore = async (docId) => {
    setActionLoading(docId)
    try {
      await api.updateDocument(docId, { is_trashed: 0 })
      setDocs(prev => prev.filter(d => d.id !== docId))
      setTotal(prev => prev - 1)
      if (previewDocId === docId) setPreviewDocId(null)
    } catch (err) {
      alert(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  const deletePermanently = async (docId) => {
    setActionLoading(docId)
    try {
      await api.deleteDocument(docId)
      setDocs(prev => prev.filter(d => d.id !== docId))
      setTotal(prev => prev - 1)
      if (previewDocId === docId) setPreviewDocId(null)
      setConfirmDeleteId(null)
    } catch (err) {
      alert(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Trash</h1>
        <div className="subtitle">
          {loading ? 'Loading…' : `${total} trashed document${total !== 1 ? 's' : ''} — hidden from search, timeline, network, and Q&A`}
        </div>
      </div>

      <div style={{ padding: '1.5rem 2rem' }}>
        {loading ? (
          <div className="loading">Loading trashed documents…</div>
        ) : docs.length === 0 ? (
          <div className="empty-state">
            <h3>Trash is empty</h3>
            <p>Documents you trash will appear here. Their full records are preserved until you permanently delete them.</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '1rem' }}>
            {docs.map(doc => {
              const title   = doc.title || doc.filename || `Document #${doc.id}`
              const date    = doc.date_depicted || doc.date_range_start || ''
              const imgUrl  = api.getDocumentImageUrl(doc.id)
              const busy    = actionLoading === doc.id
              const isConfirming = confirmDeleteId === doc.id

              return (
                <div
                  key={doc.id}
                  style={{
                    background: 'var(--cream-card)',
                    border: '1px solid var(--border)',
                    borderRadius: '4px',
                    overflow: 'hidden',
                    display: 'flex',
                    flexDirection: 'column',
                    boxShadow: '0 1px 3px var(--shadow)',
                    opacity: busy ? 0.6 : 1,
                    transition: 'opacity 0.2s',
                  }}
                >
                  {/* Image — click to preview */}
                  <div
                    onClick={() => !busy && setPreviewDocId(doc.id)}
                    style={{
                      width: '100%',
                      aspectRatio: '4/3',
                      overflow: 'hidden',
                      background: 'var(--navy-deep)',
                      cursor: 'pointer',
                      position: 'relative',
                    }}
                  >
                    <img
                      src={imgUrl}
                      alt={title}
                      style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block', filter: 'grayscale(30%)' }}
                      loading="lazy"
                      onError={e => { e.target.parentElement.style.background = 'var(--navy-mid)'; e.target.style.display = 'none' }}
                    />
                    {/* Hover hint */}
                    <div style={{
                      position: 'absolute', inset: 0,
                      background: 'rgba(15,25,35,0)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      transition: 'background 0.15s',
                      fontSize: '0.8rem', color: 'white', fontWeight: 600,
                    }}
                      onMouseEnter={e => e.currentTarget.style.background = 'rgba(15,25,35,0.35)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'rgba(15,25,35,0)'}
                    >
                    </div>
                    <div style={{
                      position: 'absolute', bottom: '0.4rem', right: '0.4rem',
                      background: 'rgba(0,0,0,0.55)', color: 'white',
                      fontSize: '0.7rem', padding: '1px 5px', borderRadius: '2px',
                    }}>
                      🔍 Preview
                    </div>
                  </div>

                  {/* Card body */}
                  <div style={{ padding: '0.65rem 0.75rem', flex: 1 }}>
                    <div style={{
                      fontFamily: 'var(--font-serif)', fontSize: '0.88rem', fontWeight: 600,
                      color: 'var(--navy)', marginBottom: '0.2rem', lineHeight: 1.3,
                      display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
                    }}>
                      {title}
                    </div>
                    <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>
                      {date || 'Date unknown'}{doc.location ? ` · ${doc.location}` : ''}
                    </div>
                  </div>

                  {/* Action buttons */}
                  <div style={{
                    borderTop: '1px solid var(--border)',
                    padding: '0.5rem 0.75rem',
                    display: 'flex',
                    gap: '0.4rem',
                  }}>
                    {isConfirming ? (
                      <>
                        <span style={{ fontSize: '0.78rem', color: 'var(--rust)', fontWeight: 600, flex: 1, alignSelf: 'center' }}>
                          Delete forever?
                        </span>
                        <button
                          onClick={() => setConfirmDeleteId(null)}
                          disabled={busy}
                          style={{ fontSize: '0.76rem', padding: '0.25rem 0.5rem', background: 'none', border: '1px solid var(--border)', borderRadius: '3px', cursor: 'pointer', color: 'var(--text-muted)', fontFamily: 'inherit' }}
                        >
                          Cancel
                        </button>
                        <button
                          onClick={() => deletePermanently(doc.id)}
                          disabled={busy}
                          style={{ fontSize: '0.76rem', padding: '0.25rem 0.5rem', background: 'var(--rust)', border: 'none', borderRadius: '3px', cursor: 'pointer', color: 'white', fontWeight: 600, fontFamily: 'inherit' }}
                        >
                          {busy ? '…' : 'Confirm'}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => restore(doc.id)}
                          disabled={busy}
                          style={{
                            flex: 1, fontSize: '0.78rem', padding: '0.3rem 0',
                            background: 'none', border: '1px solid var(--border)',
                            borderRadius: '3px', cursor: 'pointer',
                            color: 'var(--navy-light)', fontFamily: 'inherit', fontWeight: 600,
                            transition: 'all 0.15s',
                          }}
                          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--navy-light)'; e.currentTarget.style.background = 'rgba(26,35,50,0.05)' }}
                          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.background = 'none' }}
                        >
                          {busy ? '…' : '↩ Restore'}
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(doc.id)}
                          disabled={busy}
                          style={{
                            flex: 1, fontSize: '0.78rem', padding: '0.3rem 0',
                            background: 'none', border: '1px solid var(--border)',
                            borderRadius: '3px', cursor: 'pointer',
                            color: 'var(--text-muted)', fontFamily: 'inherit',
                            transition: 'all 0.15s',
                          }}
                          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--rust)'; e.currentTarget.style.color = 'var(--rust)' }}
                          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-muted)' }}
                        >
                          ✕ Delete
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {previewDocId != null && (
        <DocPreviewPanel docId={previewDocId} onClose={() => setPreviewDocId(null)} />
      )}
    </div>
  )
}
