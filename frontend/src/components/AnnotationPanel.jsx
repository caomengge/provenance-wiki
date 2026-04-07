import React, { useState, useEffect, useRef } from 'react'
import api from '../api/client'

/**
 * Auto-saving annotation panel for a document.
 * Debounces saves 1.5s after the last keystroke.
 */
export default function AnnotationPanel({ docId, initialValue = '' }) {
  const [text, setText]       = useState(initialValue)
  const [saving, setSaving]   = useState(false)
  const [saved, setSaved]     = useState(false)
  const [error, setError]     = useState('')
  const timerRef = useRef(null)

  // Sync initialValue when it changes (doc reload)
  useEffect(() => { setText(initialValue || '') }, [initialValue])

  const handleChange = (e) => {
    const val = e.target.value
    setText(val)
    setSaved(false)
    setError('')

    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => save(val), 1500)
  }

  const save = async (val) => {
    try {
      setSaving(true)
      await api.updateDocument(docId, { annotation: val })
      setSaved(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  // Flush on unmount
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])

  return (
    <div>
      <label>Researcher Annotation</label>
      <textarea
        value={text}
        onChange={handleChange}
        placeholder="Add your research notes, observations, or provenance analysis here…"
        style={{ minHeight: '100px' }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.3rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        {saving && <span>Saving…</span>}
        {saved && !saving && <span style={{ color: '#2e7d32' }}>✓ Saved</span>}
        {error && <span style={{ color: 'var(--rust)' }}>Error: {error}</span>}
        {!saving && !saved && !error && text !== initialValue && (
          <span>Unsaved changes</span>
        )}
        <span style={{ marginLeft: 'auto', color: 'var(--border)' }}>{text.length} chars</span>
      </div>
    </div>
  )
}
