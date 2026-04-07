import React, { useState, useRef, useEffect, useId } from 'react'

/**
 * Click-to-edit inline field.
 *
 * Props:
 *   value       – current value (string | null)
 *   onSave      – async fn(newValue: string | null) called on commit
 *   multiline   – use <textarea> instead of <input>
 *   placeholder – placeholder shown inside the input
 *   emptyLabel  – text shown in display mode when value is empty
 *   suggestions – string[] shown as datalist options (optional)
 */
export default function InlineEdit({
  value,
  onSave,
  multiline = false,
  placeholder = 'Click to edit…',
  emptyLabel = '—',
  suggestions = [],
}) {
  const listId = useId()
  const [editing, setEditing] = useState(false)
  const [draft,   setDraft]   = useState(value || '')
  const [saving,  setSaving]  = useState(false)
  const ref = useRef(null)

  // Sync draft when value prop changes externally
  useEffect(() => {
    if (!editing) setDraft(value || '')
  }, [value, editing])

  useEffect(() => {
    if (editing) {
      ref.current?.focus()
      if (!multiline) ref.current?.select()
    }
  }, [editing, multiline])

  const commit = async () => {
    const trimmed = draft.trim()
    const original = (value || '').trim()
    if (trimmed === original) {
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      await onSave(trimmed || null)
    } finally {
      setSaving(false)
      setEditing(false)
    }
  }

  const cancel = () => {
    setDraft(value || '')
    setEditing(false)
  }

  const inputStyle = {
    width: '100%',
    fontFamily: 'inherit',
    fontSize: 'inherit',
    lineHeight: 'inherit',
    color: 'var(--text-body)',
    background: 'var(--cream-bg)',
    border: '1px solid var(--gold)',
    borderRadius: '3px',
    padding: '0.25rem 0.4rem',
    outline: 'none',
    boxSizing: 'border-box',
    resize: multiline ? 'vertical' : 'none',
    opacity: saving ? 0.6 : 1,
  }

  if (editing) {
    return multiline ? (
      <textarea
        ref={ref}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={e => { if (e.key === 'Escape') cancel() }}
        placeholder={placeholder}
        rows={5}
        style={inputStyle}
        disabled={saving}
      />
    ) : (
      <>
        <input
          ref={ref}
          type="text"
          list={suggestions.length > 0 ? listId : undefined}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={e => {
            if (e.key === 'Enter') { e.preventDefault(); commit() }
            if (e.key === 'Escape') cancel()
          }}
          placeholder={placeholder}
          style={inputStyle}
          disabled={saving}
        />
        {suggestions.length > 0 && (
          <datalist id={listId}>
            {suggestions.map(s => <option key={s} value={s} />)}
          </datalist>
        )}
      </>
    )
  }

  return (
    <span
      onClick={() => { setDraft(value || ''); setEditing(true) }}
      title="Click to edit"
      style={{
        cursor: 'text',
        color: value ? 'var(--text-body)' : 'var(--text-light)',
        borderBottom: '1px dashed transparent',
        paddingBottom: '1px',
        display: multiline ? 'block' : 'inline',
        whiteSpace: multiline ? 'pre-wrap' : 'normal',
        lineHeight: multiline ? 1.7 : 'inherit',
        minHeight: multiline ? '1.4em' : 'auto',
        transition: 'border-color 0.15s',
      }}
      onMouseEnter={e => e.currentTarget.style.borderBottomColor = 'var(--border)'}
      onMouseLeave={e => e.currentTarget.style.borderBottomColor = 'transparent'}
    >
      {value || emptyLabel}
    </span>
  )
}
