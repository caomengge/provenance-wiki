import React, { useState, useEffect } from 'react'
import api from '../api/client'

const RELATIONSHIP_TYPES = [
  'related', 'same-object', 'sequential', 'contradicts',
  'corroborates', 'references', 'auction-lot', 'correspondence',
]

/**
 * Modal dialog for creating a link between two documents.
 * The user searches by title/ID and selects a relationship type.
 */
export default function DocumentLinkModal({ docId, onClose, onLinked }) {
  const [query, setQuery]       = useState('')
  const [results, setResults]   = useState([])
  const [selected, setSelected] = useState(null)
  const [relType, setRelType]   = useState('related')
  const [notes, setNotes]       = useState('')
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')

  useEffect(() => {
    if (!query.trim()) { setResults([]); return }
    const t = setTimeout(async () => {
      try {
        const res = await api.search({ q: query, mode: 'keyword', per_page: 10 })
        setResults((res.results || []).filter(d => d.id !== docId))
      } catch {}
    }, 350)
    return () => clearTimeout(t)
  }, [query, docId])

  const handleLink = async () => {
    if (!selected) return
    setLoading(true)
    setError('')
    try {
      await api.createLink(docId, selected.id, relType, notes || null)
      if (onLinked) onLinked({ target: selected, relationship_type: relType, notes })
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <h2>Link Document</h2>

        <div style={{ marginBottom: '1rem' }}>
          <label>Search for a document to link</label>
          <input
            type="search"
            value={query}
            onChange={e => { setQuery(e.target.value); setSelected(null) }}
            placeholder="Search by title, description, entity…"
            autoFocus
          />
        </div>

        {results.length > 0 && !selected && (
          <div style={{ border: '1px solid var(--border)', borderRadius: '3px', maxHeight: '200px', overflowY: 'auto', marginBottom: '1rem' }}>
            {results.map(d => (
              <div
                key={d.id}
                onClick={() => setSelected(d)}
                style={{
                  padding: '0.5rem 0.75rem',
                  cursor: 'pointer',
                  borderBottom: '1px solid var(--border-light)',
                  transition: 'background 0.1s',
                }}
                onMouseEnter={e => e.currentTarget.style.background = 'var(--cream-bg)'}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
              >
                <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{d.title || d.filename}</div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                  #{d.id} · {d.date_depicted || 'date unknown'} · {d.location || ''}
                </div>
              </div>
            ))}
          </div>
        )}

        {selected && (
          <div style={{ background: 'var(--cream-bg)', border: '1px solid var(--gold)', borderRadius: '3px', padding: '0.6rem 0.75rem', marginBottom: '1rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 600 }}>{selected.title || selected.filename}</div>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>#{selected.id}</div>
            </div>
            <button onClick={() => setSelected(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: '1.2rem' }}>×</button>
          </div>
        )}

        <div style={{ marginBottom: '1rem' }}>
          <label>Relationship type</label>
          <select value={relType} onChange={e => setRelType(e.target.value)}>
            {RELATIONSHIP_TYPES.map(r => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>

        <div style={{ marginBottom: '1rem' }}>
          <label>Notes (optional)</label>
          <textarea
            value={notes}
            onChange={e => setNotes(e.target.value)}
            placeholder="Explain the relationship between these documents…"
            style={{ minHeight: '60px' }}
          />
        </div>

        {error && <div style={{ color: 'var(--rust)', marginBottom: '1rem', fontSize: '0.9rem' }}>{error}</div>}

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary"
            onClick={handleLink}
            disabled={!selected || loading}
          >
            {loading ? 'Linking…' : 'Create Link'}
          </button>
        </div>
      </div>
    </div>
  )
}
