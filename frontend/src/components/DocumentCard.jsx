import React from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'

/**
 * Grid/list card for a document.
 *
 * The outer element is an <a href> so the browser provides a native context
 * menu with "Open in New Tab" on right-click, and Cmd/Ctrl+click opens a
 * new tab automatically. Plain left-click is intercepted for SPA navigation.
 *
 * When selectMode=true, navigation is suppressed and clicking toggles selection.
 */
export default function DocumentCard({ doc, view = 'grid', selectMode = false, selected = false, onToggleSelect }) {
  const navigate = useNavigate()

  const isGroup   = !!doc._isGroup
  const detailUrl = isGroup ? `/groups/${doc.id}` : `/documents/${doc.id}`

  const handleCardClick = (e) => {
    if (e.target.type === 'checkbox') return   // let checkbox handle its own event

    if (selectMode && onToggleSelect) {
      if (isGroup) return                       // groups are not selectable for batch ops
      e.preventDefault()
      onToggleSelect(doc.id)
      return
    }

    // Modifier keys / middle-click → let the browser open a new tab naturally
    if (e.ctrlKey || e.metaKey || e.shiftKey) return

    e.preventDefault()
    navigate(detailUrl)
  }

  const handleCheckboxClick = (e) => {
    e.stopPropagation()
    if (onToggleSelect) onToggleSelect(doc.id)
  }

  const title  = doc.title || doc.filename || (isGroup ? `Group #${doc.id}` : `Document #${doc.id}`)
  const date   = doc.date_depicted || doc.date_range_start || ''
  const imgUrl = isGroup && doc.first_page_id
    ? api.getDocumentImageUrl(doc.first_page_id)
    : api.getDocumentImageUrl(doc.id)
  const href   = detailUrl

  // ── List view ──────────────────────────────────────────────────────────────
  if (view === 'list') {
    return (
      <a
        href={href}
        onClick={handleCardClick}
        style={{
          display:       'flex',
          gap:           '0.75rem',
          padding:       '0.75rem',
          background:    selected ? 'rgba(201,168,76,0.06)' : 'var(--cream-card)',
          border:        `1px solid ${selected ? 'var(--gold)' : 'var(--border)'}`,
          borderRadius:  '3px',
          cursor:        'pointer',
          marginBottom:  '0.4rem',
          transition:    'all 0.15s',
          alignItems:    'flex-start',
          textDecoration: 'none',
          color:          'inherit',
        }}
        onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'var(--cream-bg)' }}
        onMouseLeave={e => { if (!selected) e.currentTarget.style.background = selected ? 'rgba(201,168,76,0.06)' : 'var(--cream-card)' }}
      >
        {selectMode && (
          <input
            type="checkbox"
            checked={selected}
            onChange={handleCheckboxClick}
            onClick={e => e.stopPropagation()}
            style={{ marginTop: '4px', flexShrink: 0, cursor: 'pointer', width: '16px', height: '16px' }}
          />
        )}
        <img
          src={imgUrl}
          alt={title}
          style={{ width: '80px', height: '60px', objectFit: 'cover', borderRadius: '2px', flexShrink: 0 }}
          onError={(e) => { e.target.style.display = 'none' }}
        />
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: '0.95rem', color: 'var(--navy)' }}>
            {doc.is_key_evidence ? <span style={{ color: 'var(--rust)', marginRight: '0.3em' }}>★</span> : null}
            {title}
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            {date && <span>{date}</span>}
            {doc.location && <span style={{ marginLeft: '0.5rem' }}>· {doc.location}</span>}
            {doc.medium  && <span style={{ marginLeft: '0.5rem' }}>· {doc.medium}</span>}
          </div>
          {doc.snippet && (
            <div
              style={{ fontSize: '0.83rem', color: 'var(--text-body)', marginTop: '0.25rem', fontStyle: 'italic' }}
              dangerouslySetInnerHTML={{ __html: doc.snippet }}
            />
          )}
          {doc.annotation && (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
              Note: {doc.annotation.substring(0, 100)}{doc.annotation.length > 100 ? '…' : ''}
            </div>
          )}
        </div>
      </a>
    )
  }

  // ── Grid view ──────────────────────────────────────────────────────────────
  return (
    <a
      href={href}
      onClick={handleCardClick}
      style={{
        background:     'var(--cream-card)',
        border:         `1px solid ${selected ? 'var(--gold)' : 'var(--border)'}`,
        borderRadius:   '4px',
        overflow:       'hidden',
        cursor:         'pointer',
        display:        'flex',
        flexDirection:  'column',
        boxShadow:      selected ? '0 0 0 2px var(--gold-dark)' : '0 1px 3px var(--shadow)',
        transition:     'box-shadow 0.2s, border-color 0.2s',
        position:       'relative',
        textDecoration: 'none',
        color:          'inherit',
      }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.boxShadow = '0 4px 12px var(--shadow-deep)' }}
      onMouseLeave={e => { e.currentTarget.style.boxShadow = selected ? '0 0 0 2px var(--gold-dark)' : '0 1px 3px var(--shadow)' }}
    >
      {/* Image area */}
      <div style={{ width: '100%', aspectRatio: '4/3', overflow: 'hidden', background: 'var(--navy-deep)', position: 'relative' }}>
        <img
          src={imgUrl}
          alt={title}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
          loading="lazy"
          onError={(e) => { e.target.parentElement.style.background = 'var(--navy-mid)'; e.target.style.display = 'none' }}
        />

        {/* Group badge */}
        {isGroup && (
          <span style={{
            position: 'absolute', top: '0.4rem', left: '0.4rem',
            background: 'var(--navy)', color: 'white',
            fontSize: '0.7rem', fontWeight: 700, padding: '1px 5px',
            borderRadius: '2px', letterSpacing: '0.04em',
          }}>⊞ {doc.page_count} pages</span>
        )}

        {/* Key evidence badge */}
        {doc.is_key_evidence ? (
          <span style={{
            position: 'absolute', top: '0.4rem', right: '0.4rem',
            background: 'var(--rust)', color: 'white',
            fontSize: '0.7rem', fontWeight: 700, padding: '1px 5px',
            borderRadius: '2px', letterSpacing: '0.05em',
          }}>★ KEY</span>
        ) : null}

        {/* Selection checkbox overlay (only visible in selectMode) */}
        {selectMode && (
          <div
            onClick={handleCheckboxClick}
            style={{
              position: 'absolute', top: '0.4rem', left: '0.4rem',
              background: selected ? 'var(--gold)' : 'rgba(255,255,255,0.9)',
              border: `2px solid ${selected ? 'var(--gold-dark)' : 'var(--border)'}`,
              borderRadius: '3px', width: '22px', height: '22px',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              cursor: 'pointer', fontSize: '13px', fontWeight: 700,
              color: 'var(--navy)', transition: 'all 0.15s',
            }}
          >
            {selected ? '✓' : ''}
          </div>
        )}
      </div>

      {/* Card body */}
      <div style={{ padding: '0.75rem', flex: 1 }}>
        <div style={{
          fontFamily: 'var(--font-serif)', fontSize: '0.9rem', fontWeight: 600,
          color: 'var(--navy)', marginBottom: '0.25rem', lineHeight: 1.3,
          display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
        }}>
          {title}
        </div>
        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
          {date || 'Date unknown'}
          {doc.location ? ` · ${doc.location}` : ''}
        </div>
        {doc.medium && (
          <div style={{ fontSize: '0.76rem', color: 'var(--text-light)', marginTop: '0.2rem' }}>
            {doc.medium}
          </div>
        )}
      </div>
    </a>
  )
}
