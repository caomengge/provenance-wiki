import React, { useState, useEffect, useRef } from 'react'

/**
 * Read-only transcription view with an explicit Edit / Save / Cancel flow.
 *
 * Default state shows the text as a <pre> with an "Edit" button in the top
 * action row. Clicking Edit swaps to a textarea; Save commits via onSave,
 * Cancel reverts to the last saved value.
 *
 * Props:
 *   value         – current transcription string (or null/empty)
 *   onSave(text)  – async fn called with the new text (null when cleared)
 *   emptyLabel    – text shown when value is empty and not editing
 *   placeholder   – placeholder for the textarea
 */
export default function TranscriptionEditor({
  value,
  onSave,
  emptyLabel = 'No transcription.',
  placeholder = 'Type or paste transcription…',
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft]     = useState(value || '')
  const [saving, setSaving]   = useState(false)
  const taRef = useRef(null)

  useEffect(() => {
    if (!editing) setDraft(value || '')
  }, [value, editing])

  useEffect(() => {
    if (editing) taRef.current?.focus()
  }, [editing])

  const startEdit = () => { setDraft(value || ''); setEditing(true) }

  const cancel = () => { setDraft(value || ''); setEditing(false) }

  const save = async () => {
    const trimmed = draft.trim()
    const original = (value || '').trim()
    if (trimmed === original) { setEditing(false); return }
    setSaving(true)
    try {
      await onSave(trimmed || null)
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); cancel() }
    // Cmd/Ctrl+Enter saves; plain Enter inserts a newline.
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save() }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginBottom: '0.5rem' }}>
        {!editing && (
          <button className="btn btn-ghost" onClick={startEdit} style={{ fontSize: '0.82rem', padding: '0.25rem 0.6rem' }}>
            ✎ Edit
          </button>
        )}
        {editing && (
          <>
            <button
              className="btn btn-ghost"
              onClick={cancel}
              disabled={saving}
              style={{ fontSize: '0.82rem', padding: '0.25rem 0.6rem' }}
            >
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={save}
              disabled={saving}
              style={{ fontSize: '0.82rem', padding: '0.25rem 0.6rem' }}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </>
        )}
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {editing ? (
          <textarea
            ref={taRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            style={{
              width: '100%',
              height: '100%',
              minHeight: '300px',
              boxSizing: 'border-box',
              fontFamily: 'inherit',
              fontSize: '0.92rem',
              lineHeight: 1.8,
              color: 'var(--text-body)',
              padding: '0.5rem',
              border: '1px solid var(--border)',
              borderRadius: '3px',
              resize: 'vertical',
            }}
          />
        ) : value ? (
          <pre style={{
            margin: 0,
            fontFamily: 'inherit',
            fontSize: '0.92rem',
            lineHeight: 1.8,
            color: 'var(--text-body)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}>
            {value}
          </pre>
        ) : (
          <p style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>{emptyLabel}</p>
        )}
      </div>
    </div>
  )
}
