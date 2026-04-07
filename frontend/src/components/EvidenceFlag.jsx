import React, { useState } from 'react'
import api from '../api/client'

/**
 * Toggle star button for marking a document as key evidence.
 * Persists to the API on click.
 */
export default function EvidenceFlag({ docId, initial = false, onToggle }) {
  const [flagged, setFlagged] = useState(initial)
  const [loading, setLoading] = useState(false)

  const toggle = async (e) => {
    e.stopPropagation()
    if (loading) return
    const next = !flagged
    setLoading(true)
    try {
      await api.updateDocument(docId, { is_key_evidence: next ? 1 : 0 })
      setFlagged(next)
      if (onToggle) onToggle(next)
    } catch (err) {
      console.error('Failed to update key evidence flag:', err)
    } finally {
      setLoading(false)
    }
  }

  return (
    <button
      onClick={toggle}
      disabled={loading}
      title={flagged ? 'Marked as key evidence — click to remove' : 'Mark as key evidence'}
      style={{
        background: flagged ? 'var(--rust)' : 'transparent',
        color: flagged ? 'white' : 'var(--text-muted)',
        border: `1px solid ${flagged ? 'var(--rust)' : 'var(--border)'}`,
        borderRadius: '3px',
        padding: '0.3rem 0.7rem',
        cursor: loading ? 'not-allowed' : 'pointer',
        fontFamily: 'var(--font-serif)',
        fontSize: '0.85rem',
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.35rem',
        transition: 'all 0.15s',
        opacity: loading ? 0.6 : 1,
      }}
    >
      ★ {flagged ? 'Key Evidence' : 'Mark Key Evidence'}
    </button>
  )
}
