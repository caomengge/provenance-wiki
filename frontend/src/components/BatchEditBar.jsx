/**
 * BatchEditBar.jsx
 *
 * Shown instead of the normal action row whenever documents are selected.
 * Lets the user batch-apply a Source label or add a Tag to all selected docs.
 *
 * Props
 *   selectedIds   Set<number>  — IDs of currently-selected documents
 *   onClear       ()=>void     — exits select mode (clears selection)
 *   onExport      ()=>void     — triggers PDF export of selection
 *   exporting     boolean
 *   archives      string[]     — existing archive names (for autocomplete)
 *   onDone        ()=>void     — called after a successful batch edit so the
 *                               parent can reload its document list
 */

import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'

const TAG_COLORS = [
  '#c9a84c','#8b3a2e','#2e5f8b','#2e8b57','#6b4c8b',
  '#8b6914','#4a6741','#7a3f4e','#3d6b7a','#7a5c3d',
]

const PANEL_STYLE = {
  background:   'var(--cream-bg)',
  border:       '1px solid var(--border)',
  borderRadius: '4px',
  padding:      '0.75rem',
  marginTop:    '0.5rem',
}

export default function BatchEditBar({
  selectedIds,
  onClear,
  onExport,
  exporting   = false,
  archives    = [],
  onDone,
}) {
  const count    = selectedIds.size
  const navigate = useNavigate()

  // Which panel is open: null | 'source' | 'tag' | 'group'
  const [panel, setPanel] = useState(null)

  // Group panel state
  const [groupTitle,    setGroupTitle]    = useState('')
  const [groupCreating, setGroupCreating] = useState(false)

  // Source panel state
  const [sourceValue,    setSourceValue]    = useState('')
  const [sourceApplying, setSourceApplying] = useState(false)

  // Tag panel state
  const [allTags,      setAllTags]      = useState([])
  const [newTagName,   setNewTagName]   = useState('')
  const [newTagColor,  setNewTagColor]  = useState(TAG_COLORS[0])
  const [tagApplying,  setTagApplying]  = useState(null)   // tagId | 'new' | null

  // Load tags when the tag panel opens
  useEffect(() => {
    if (panel === 'tag') {
      api.getTags().then(r => setAllTags(r.tags || [])).catch(() => {})
    }
  }, [panel])

  // Reset subpanel state when closing
  const togglePanel = (p) => {
    setPanel(prev => {
      if (prev === p) { resetPanelState(p); return null }
      resetPanelState(prev)
      return p
    })
  }

  const resetPanelState = (p) => {
    if (p === 'source') { setSourceValue(''); setSourceApplying(false) }
    if (p === 'tag')    { setNewTagName(''); setTagApplying(null) }
    if (p === 'group')  { setGroupTitle(''); setGroupCreating(false) }
  }

  // ── Source batch-apply ─────────────────────────────────────────────────────

  const applySource = async () => {
    if (sourceApplying) return
    setSourceApplying(true)
    try {
      const value = sourceValue.trim() || null
      await Promise.all([...selectedIds].map(id =>
        api.updateDocument(id, { source_archive: value })
      ))
      setPanel(null)
      setSourceValue('')
      onDone()
    } catch (err) {
      alert(err.message)
    } finally {
      setSourceApplying(false)
    }
  }

  // ── Tag batch-apply ────────────────────────────────────────────────────────

  const applyTag = async (tagId) => {
    if (tagApplying != null) return
    setTagApplying(tagId)
    try {
      await Promise.all([...selectedIds].map(id => api.addDocTag(id, tagId)))
      onDone()
    } catch (err) {
      alert(err.message)
    } finally {
      setTagApplying(null)
    }
  }

  const createAndApplyTag = async () => {
    if (!newTagName.trim() || tagApplying != null) return
    setTagApplying('new')
    try {
      const tag = await api.createTag(newTagName.trim(), newTagColor)
      setAllTags(prev => [...prev, tag])
      await Promise.all([...selectedIds].map(id => api.addDocTag(id, tag.id)))
      setNewTagName('')
      onDone()
    } catch (err) {
      alert(err.message)
    } finally {
      setTagApplying(null)
    }
  }

  // ── Group create ───────────────────────────────────────────────────────────

  const createGroup = async () => {
    if (groupCreating) return
    setGroupCreating(true)
    try {
      const result = await api.createGroup([...selectedIds], groupTitle.trim() || undefined)
      navigate(`/groups/${result.group_id}`)
    } catch (err) {
      alert(err.message)
      setGroupCreating(false)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div>
      {/* ── Top action row ──────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '0.88rem', color: 'var(--text-muted)', flexShrink: 0 }}>
          {count} selected
        </span>

        <button
          className={`btn ${panel === 'source' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => togglePanel('source')}
          style={{ fontSize: '0.82rem' }}
        >
          ✎ Set Source
        </button>

        <button
          className={`btn ${panel === 'tag' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => togglePanel('tag')}
          style={{ fontSize: '0.82rem' }}
        >
          + Add Tag
        </button>

        <button
          className="btn btn-gold"
          onClick={onExport}
          disabled={exporting || count === 0}
        >
          {exporting ? '…' : '↓ Export PDF'}
        </button>

        {count >= 2 && (
          <button
            className={`btn ${panel === 'group' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => togglePanel('group')}
            style={{ fontSize: '0.82rem' }}
          >
            ⊞ Group Pages
          </button>
        )}

        <button className="btn btn-ghost" onClick={onClear}>
          Cancel
        </button>
      </div>

      {/* ── Source panel ────────────────────────────────────────────────── */}
      {panel === 'source' && (
        <div style={PANEL_STYLE}>
          <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
            Set source archive for <strong>{count}</strong> document{count !== 1 ? 's' : ''}:
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              list="batch-archive-datalist"
              value={sourceValue}
              onChange={e => setSourceValue(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter')  applySource()
                if (e.key === 'Escape') togglePanel('source')
              }}
              placeholder="Archive / institution name (leave blank to clear)…"
              autoFocus
              style={{ flex: 1, minWidth: '220px', fontSize: '0.88rem' }}
            />
            <datalist id="batch-archive-datalist">
              {archives.map(a => <option key={a} value={a} />)}
            </datalist>
            {sourceValue.trim() && (
              <button
                onClick={() => setSourceValue('')}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.82rem', color: 'var(--text-muted)' }}
              >✕</button>
            )}
            <button
              className="btn btn-primary"
              onClick={applySource}
              disabled={sourceApplying}
              style={{ fontSize: '0.82rem', flexShrink: 0 }}
            >
              {sourceApplying ? 'Applying…' : 'Apply to all'}
            </button>
          </div>
        </div>
      )}

      {/* ── Tag panel ───────────────────────────────────────────────────── */}
      {panel === 'tag' && (
        <div style={PANEL_STYLE}>
          <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
            Add tag to <strong>{count}</strong> document{count !== 1 ? 's' : ''}:
          </div>

          {/* Existing tags — click to add to all */}
          {allTags.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem', marginBottom: '0.65rem' }}>
              {allTags.map(tag => (
                <button
                  key={tag.id}
                  onClick={() => applyTag(tag.id)}
                  disabled={tagApplying != null}
                  title={`Add "${tag.name}" to all ${count} selected documents`}
                  style={{
                    display:    'inline-flex',
                    alignItems: 'center',
                    gap:        '0.3rem',
                    background: tag.color + '22',
                    border:     `1px solid ${tag.color}66`,
                    borderRadius: '3px',
                    padding:    '0.15rem 0.55rem',
                    fontSize:   '0.82rem',
                    cursor:     tagApplying != null ? 'wait' : 'pointer',
                    opacity:    tagApplying != null && tagApplying !== tag.id ? 0.45 : 1,
                    transition: 'opacity 0.15s',
                  }}
                >
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: tag.color, flexShrink: 0 }} />
                  {tagApplying === tag.id ? '…' : tag.name}
                </button>
              ))}
            </div>
          )}

          {/* Create & apply a new tag */}
          <div style={{
            display:    'flex',
            gap:        '0.4rem',
            alignItems: 'center',
            flexWrap:   'wrap',
            borderTop:  allTags.length > 0 ? '1px solid var(--border)' : 'none',
            paddingTop: allTags.length > 0 ? '0.55rem' : 0,
          }}>
            <input
              value={newTagName}
              onChange={e => setNewTagName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') createAndApplyTag() }}
              placeholder="New tag name…"
              style={{ flex: 1, minWidth: '140px', fontSize: '0.85rem' }}
            />
            <div style={{ display: 'flex', gap: '0.2rem' }}>
              {TAG_COLORS.map(c => (
                <button
                  key={c}
                  onClick={() => setNewTagColor(c)}
                  style={{
                    width:        '16px',
                    height:       '16px',
                    borderRadius: '50%',
                    background:   c,
                    border:       c === newTagColor ? '2px solid var(--navy)' : '1px solid transparent',
                    cursor:       'pointer',
                    padding:      0,
                    flexShrink:   0,
                  }}
                />
              ))}
            </div>
            <button
              className="btn btn-primary"
              onClick={createAndApplyTag}
              disabled={!newTagName.trim() || tagApplying != null}
              style={{ fontSize: '0.82rem', flexShrink: 0 }}
            >
              {tagApplying === 'new' ? '…' : '+ Create & Add'}
            </button>
          </div>
        </div>
      )}
      {/* ── Group panel ─────────────────────────────────────────────────── */}
      {panel === 'group' && (
        <div style={PANEL_STYLE}>
          <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
            Group <strong>{count}</strong> pages into one multi-page document.
            Claude will read all pages together in a single API call.
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              value={groupTitle}
              onChange={e => setGroupTitle(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') createGroup() }}
              placeholder="Title (optional — Claude will suggest one)…"
              autoFocus
              disabled={groupCreating}
              style={{ flex: 1, minWidth: '220px', fontSize: '0.88rem' }}
            />
            <button
              className="btn btn-primary"
              onClick={createGroup}
              disabled={groupCreating}
              style={{ fontSize: '0.82rem', flexShrink: 0 }}
            >
              {groupCreating ? 'Grouping & extracting…' : `Group ${count} Pages`}
            </button>
          </div>
          {groupCreating && (
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.4rem' }}>
              Sending all pages to Claude — this may take 10–30 seconds…
            </div>
          )}
        </div>
      )}
    </div>
  )
}
