import React, { useState, useEffect, useRef } from 'react'
import api from '../api/client'

/**
 * Multi-select tag filter for the Search page.
 *
 * Loads the full tag list once (small), then offers a typeahead. Selected
 * tags appear as colored chips with × to remove. onChange fires with the
 * full array of selected tag objects whenever the selection changes.
 *
 * Props:
 *   value          – array of selected tag ids (string or number)
 *   onChange(tags) – called with the array of selected tag objects
 */
export default function TagFilter({ value = [], onChange }) {
  const [allTags, setAllTags] = useState([])
  const [query, setQuery]     = useState('')
  const [open, setOpen]       = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const wrapRef  = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    api.getTags().then(r => setAllTags(r.tags || [])).catch(() => setAllTags([]))
  }, [])

  useEffect(() => {
    const onDocClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  const selectedIds = new Set(value.map(String))
  const selectedTags = allTags.filter(t => selectedIds.has(String(t.id)))

  const q = query.trim().toLowerCase()
  const matches = allTags
    .filter(t => !selectedIds.has(String(t.id)))
    .filter(t => !q || t.name.toLowerCase().includes(q))
    .slice(0, 12)

  const add = (tag) => {
    onChange([...selectedTags, tag])
    setQuery('')
    setActiveIdx(-1)
    inputRef.current?.focus()
  }

  const remove = (tagId) => {
    onChange(selectedTags.filter(t => String(t.id) !== String(tagId)))
  }

  const handleKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActiveIdx(i => Math.min(matches.length - 1, i + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIdx(i => Math.max(-1, i - 1))
    } else if (e.key === 'Enter') {
      const target = activeIdx >= 0 ? matches[activeIdx] : matches[0]
      if (open && target) {
        e.preventDefault()
        add(target)
      }
    } else if (e.key === 'Backspace' && !query && selectedTags.length > 0) {
      // Empty input + Backspace removes the last selected chip.
      remove(selectedTags[selectedTags.length - 1].id)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative', minWidth: '220px' }}>
      <div
        onClick={() => { inputRef.current?.focus(); setOpen(true) }}
        style={{
          display: 'flex', flexWrap: 'wrap', gap: '0.3rem',
          alignItems: 'center', padding: '0.2rem 0.4rem',
          border: '1px solid var(--border)', borderRadius: '3px',
          background: 'var(--cream-card)', cursor: 'text',
          fontSize: '0.85rem',
        }}
      >
        {selectedTags.map(t => (
          <span
            key={t.id}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '0.25rem',
              background: t.color + '22', border: `1px solid ${t.color}66`,
              borderRadius: '3px', padding: '0.1rem 0.4rem', fontSize: '0.8rem',
            }}
          >
            <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: t.color, display: 'inline-block' }} />
            {t.name}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); remove(t.id) }}
              aria-label={`Remove tag ${t.name}`}
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--text-muted)', fontSize: '0.85rem', lineHeight: 1 }}
            >×</button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true); setActiveIdx(-1) }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={selectedTags.length === 0 ? 'Filter by tag…' : ''}
          style={{
            flex: 1, minWidth: '90px', border: 'none', outline: 'none',
            background: 'transparent', fontSize: '0.85rem', padding: '0.15rem 0.2rem',
          }}
        />
      </div>

      {open && matches.length > 0 && (
        <ul
          role="listbox"
          style={{
            position: 'absolute', top: '100%', left: 0, right: 0, marginTop: '2px',
            maxHeight: '240px', overflowY: 'auto',
            background: 'var(--cream-card)', border: '1px solid var(--border)',
            borderRadius: '3px', boxShadow: '0 4px 12px var(--shadow)',
            zIndex: 25, listStyle: 'none', margin: 0, padding: '4px 0',
            fontSize: '0.85rem',
          }}
        >
          {matches.map((t, i) => (
            <li
              key={t.id}
              role="option"
              aria-selected={i === activeIdx}
              onMouseDown={(e) => { e.preventDefault(); add(t) }}
              onMouseEnter={() => setActiveIdx(i)}
              style={{
                padding: '0.35rem 0.6rem', cursor: 'pointer',
                background: i === activeIdx ? 'var(--cream-bg)' : 'transparent',
                display: 'flex', alignItems: 'center', gap: '0.4rem',
              }}
            >
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: t.color, display: 'inline-block' }} />
              {t.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
