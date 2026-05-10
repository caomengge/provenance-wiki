import React, { useState, useEffect, useRef, useCallback } from 'react'
import api from '../api/client'

/**
 * Autocomplete text input for the entity-name field in "Add Entity" dialogs.
 *
 * Unlike EntityCombobox (which is a strict picker bound to an id), this is a
 * free-text input that *suggests* existing entities so the user can avoid
 * creating duplicates. Picking a suggestion fires onPick(entity) — the parent
 * typically uses that to also auto-fill the type select.
 *
 * Props:
 *   value          – current text in the input
 *   onChange(text) – called on every keystroke
 *   onPick(entity) – called when the user selects an existing entity from the
 *                    suggestion list. The parent should setName(entity.name)
 *                    and setType(entity.type).
 *   onEnter()      – called when Enter is pressed and no suggestion is
 *                    highlighted (so the parent can submit the form)
 *   onEscape()     – called when Escape is pressed and the popover is closed
 *   placeholder    – input placeholder
 *   inputRef       – optional ref forwarded onto the underlying <input>
 *   style          – optional outer style overrides
 */
export default function EntityNameAutocomplete({
  value,
  onChange,
  onPick,
  onEnter,
  onEscape,
  placeholder = 'Entity name…',
  inputRef,
  style,
}) {
  const [results, setResults]     = useState([])
  const [open, setOpen]           = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const [loading, setLoading]     = useState(false)

  const wrapRef     = useRef(null)
  const debounceRef = useRef(null)
  const localRef    = useRef(null)
  const ref         = inputRef || localRef

  useEffect(() => {
    const onDocClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  const runSearch = useCallback((q) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    const trimmed = q.trim()
    if (!trimmed) {
      setResults([])
      setActiveIdx(-1)
      return
    }
    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const r = await api.getEntities({ q: trimmed, per_page: 8 })
        setResults(r.entities || [])
        setActiveIdx(-1)
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 200)
  }, [])

  const handleChange = (e) => {
    const v = e.target.value
    onChange(v)
    setOpen(true)
    runSearch(v)
  }

  const handleFocus = () => {
    if (value && value.trim()) {
      setOpen(true)
      runSearch(value)
    }
  }

  const pick = (entity) => {
    setOpen(false)
    setResults([])
    onPick?.(entity)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      if (results.length === 0) return
      e.preventDefault()
      setOpen(true)
      setActiveIdx(i => Math.min(results.length - 1, i + 1))
    } else if (e.key === 'ArrowUp') {
      if (results.length === 0) return
      e.preventDefault()
      setActiveIdx(i => Math.max(-1, i - 1))
    } else if (e.key === 'Enter') {
      if (open && activeIdx >= 0 && results[activeIdx]) {
        // User explicitly highlighted a suggestion — pick it instead of submitting.
        e.preventDefault()
        pick(results[activeIdx])
      } else {
        // No active suggestion: fall through to the parent's submit handler.
        setOpen(false)
        onEnter?.()
      }
    } else if (e.key === 'Escape') {
      if (open) {
        setOpen(false)
      } else {
        onEscape?.()
      }
    }
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative', ...style }}>
      <input
        ref={ref}
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={handleChange}
        onFocus={handleFocus}
        onKeyDown={handleKeyDown}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        aria-controls="entity-name-ac-list"
        aria-activedescendant={activeIdx >= 0 ? `entity-name-ac-opt-${results[activeIdx]?.id}` : undefined}
        style={{ width: '100%', fontSize: '0.88rem', boxSizing: 'border-box' }}
      />

      {open && (loading || results.length > 0) && (
        <ul
          id="entity-name-ac-list"
          role="listbox"
          style={{
            position:    'absolute',
            top:         '100%',
            left:        0,
            right:       0,
            marginTop:   '2px',
            maxHeight:   '220px',
            overflowY:   'auto',
            background:  'var(--cream-card)',
            border:      '1px solid var(--border)',
            borderRadius:'3px',
            boxShadow:   '0 4px 12px var(--shadow)',
            zIndex:      30,
            listStyle:   'none',
            margin:      0,
            padding:     '4px 0',
            fontSize:    '0.85rem',
          }}
        >
          {loading && results.length === 0 && (
            <li style={{ padding: '0.4rem 0.6rem', color: 'var(--text-muted)' }}>Searching…</li>
          )}
          {results.map((e, i) => (
            <li
              key={e.id}
              id={`entity-name-ac-opt-${e.id}`}
              role="option"
              aria-selected={i === activeIdx}
              onMouseDown={(ev) => { ev.preventDefault(); pick(e) }}
              onMouseEnter={() => setActiveIdx(i)}
              style={{
                padding:    '0.35rem 0.6rem',
                cursor:     'pointer',
                background: i === activeIdx ? 'var(--cream-bg)' : 'transparent',
                display:    'flex',
                justifyContent: 'space-between',
                gap:        '0.5rem',
              }}
            >
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {e.name}
              </span>
              <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem', flexShrink: 0 }}>
                {e.type}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
