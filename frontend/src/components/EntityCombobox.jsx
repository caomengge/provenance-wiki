import React, { useState, useEffect, useRef, useCallback } from 'react'
import api from '../api/client'

/**
 * Typeahead combobox for picking an entity from the (potentially huge)
 * entities table. Replaces a native <select> with all-entities-preloaded
 * which doesn't scale past a few hundred options.
 *
 * Props:
 *   value         – currently selected entity id (string or number), or ''
 *   onChange(id)  – called with the new id ('' to clear)
 *   placeholder   – optional placeholder text (default: "All entities")
 *   style         – optional outer style overrides
 */
export default function EntityCombobox({ value, onChange, placeholder = 'All entities', style }) {
  const [query, setQuery]         = useState('')
  const [results, setResults]     = useState([])
  const [open, setOpen]           = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const [loading, setLoading]     = useState(false)
  const [selectedName, setSelectedName] = useState('')

  const wrapRef  = useRef(null)
  const inputRef = useRef(null)
  const debounceRef = useRef(null)

  // If parent clears the value externally, reset the visible input
  useEffect(() => {
    if (!value) {
      setSelectedName('')
      setQuery('')
    }
  }, [value])

  // If a value comes in but we don't know its name (e.g. restored from URL),
  // fetch it once so the input shows something meaningful.
  useEffect(() => {
    if (value && !selectedName) {
      api.getEntity?.(value)
        ?.then(e => { if (e?.name) setSelectedName(e.name) })
        ?.catch(() => {})
    }
  }, [value, selectedName])

  // Click-outside closes the popover
  useEffect(() => {
    const onDocClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  // Debounced server-side search
  const runSearch = useCallback((q) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const r = await api.getEntities({ q: q.trim(), per_page: 20 })
        setResults(r.entities || [])
        setActiveIdx(-1)
      } catch {
        setResults([])
      } finally {
        setLoading(false)
      }
    }, 200)
  }, [])

  const handleFocus = () => {
    setOpen(true)
    if (results.length === 0) runSearch(query)
  }

  const handleChange = (e) => {
    const v = e.target.value
    setQuery(v)
    setOpen(true)
    runSearch(v)
  }

  const select = (entity) => {
    setSelectedName(entity.name)
    setQuery('')
    setOpen(false)
    onChange(entity.id)
  }

  const clear = (e) => {
    e.stopPropagation()
    setSelectedName('')
    setQuery('')
    setResults([])
    onChange('')
    inputRef.current?.focus()
  }

  const handleKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActiveIdx(i => Math.min(results.length - 1, i + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => Math.max(-1, i - 1))
    } else if (e.key === 'Enter') {
      if (open && activeIdx >= 0 && results[activeIdx]) {
        e.preventDefault()
        select(results[activeIdx])
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
      inputRef.current?.blur()
    }
  }

  const displayValue = open ? query : (selectedName || '')

  return (
    <div ref={wrapRef} style={{ position: 'relative', minWidth: '200px', ...style }}>
      <div style={{ position: 'relative' }}>
        <input
          ref={inputRef}
          type="text"
          value={displayValue}
          placeholder={placeholder}
          onFocus={handleFocus}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          aria-controls="entity-combobox-list"
          aria-activedescendant={activeIdx >= 0 ? `entity-opt-${results[activeIdx]?.id}` : undefined}
          style={{ width: '100%', paddingRight: selectedName ? '24px' : '8px' }}
        />
        {selectedName && (
          <button
            type="button"
            onClick={clear}
            aria-label="Clear filter"
            tabIndex={-1}
            style={{
              position: 'absolute', right: '4px', top: '50%', transform: 'translateY(-50%)',
              background: 'none', border: 'none', cursor: 'pointer',
              fontSize: '0.9rem', color: 'var(--text-muted)', padding: '0 4px',
            }}
          >×</button>
        )}
      </div>

      {open && (
        <ul
          id="entity-combobox-list"
          role="listbox"
          style={{
            position:    'absolute',
            top:         '100%',
            left:        0,
            right:       0,
            marginTop:   '2px',
            maxHeight:   '260px',
            overflowY:   'auto',
            background:  'var(--cream-card)',
            border:      '1px solid var(--border)',
            borderRadius:'3px',
            boxShadow:   '0 4px 12px var(--shadow)',
            zIndex:      20,
            listStyle:   'none',
            margin:      0,
            padding:     '4px 0',
            fontSize:    '0.88rem',
          }}
        >
          {loading && results.length === 0 && (
            <li style={{ padding: '0.4rem 0.6rem', color: 'var(--text-muted)' }}>Searching…</li>
          )}
          {!loading && results.length === 0 && (
            <li style={{ padding: '0.4rem 0.6rem', color: 'var(--text-muted)' }}>
              {query ? 'No matches' : 'Type to search…'}
            </li>
          )}
          {results.map((e, i) => (
            <li
              key={e.id}
              id={`entity-opt-${e.id}`}
              role="option"
              aria-selected={i === activeIdx}
              onMouseDown={(ev) => { ev.preventDefault(); select(e) }}
              onMouseEnter={() => setActiveIdx(i)}
              style={{
                padding:    '0.4rem 0.6rem',
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
