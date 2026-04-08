import React, { useState, useEffect } from 'react'
import api from '../api/client'

const TAG_COLORS = [
  '#c9a84c','#8b3a2e','#2e5f8b','#2e8b57','#6b4c8b',
  '#8b6914','#4a6741','#7a3f4e','#3d6b7a','#7a5c3d',
]

/** Tag manager for a document or group: view/add/remove tags inline.
 *  Pass isGroup=true + groupId=<id> to manage group tags. */
export default function TagManager({ docId, initialTags = [], isGroup = false, groupId }) {
  const [tags, setTags]         = useState(initialTags)
  const [allTags, setAllTags]   = useState([])
  const [newName, setNewName]   = useState('')
  const [newColor, setNewColor] = useState(TAG_COLORS[0])
  const [adding, setAdding]     = useState(false)
  const [showAdd, setShowAdd]   = useState(false)

  useEffect(() => { setTags(initialTags) }, [initialTags])

  useEffect(() => {
    api.getTags().then(r => setAllTags(r.tags || [])).catch(() => {})
  }, [])

  const addExisting = async (tag) => {
    if (tags.find(t => t.id === tag.id)) return
    try {
      if (isGroup) {
        await api.addGroupTag(groupId, tag.id)
      } else {
        await api.addDocTag(docId, tag.id)
      }
      setTags(prev => [...prev, tag])
    } catch (err) {
      console.error(err)
    }
  }

  const createAndAdd = async () => {
    if (!newName.trim()) return
    setAdding(true)
    try {
      const tag = await api.createTag(newName.trim(), newColor)
      if (isGroup) {
        await api.addGroupTag(groupId, tag.id)
      } else {
        await api.addDocTag(docId, tag.id)
      }
      setTags(prev => [...prev, tag])
      setAllTags(prev => [...prev, tag])
      setNewName('')
      setShowAdd(false)
    } catch (err) {
      console.error(err)
    } finally {
      setAdding(false)
    }
  }

  const removeTag = async (tagId) => {
    try {
      if (isGroup) {
        await api.removeGroupTag(groupId, tagId)
      } else {
        await api.removeDocTag(docId, tagId)
      }
      setTags(prev => prev.filter(t => t.id !== tagId))
    } catch (err) {
      console.error(err)
    }
  }

  const available = allTags.filter(t => !tags.find(dt => dt.id === t.id))

  return (
    <div>
      <label>Tags</label>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.5rem' }}>
        {tags.map(tag => (
          <span
            key={tag.id}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.25rem',
              background: tag.color + '22',
              border: `1px solid ${tag.color}66`,
              borderRadius: '3px',
              padding: '0.15rem 0.5rem',
              fontSize: '0.82rem',
              color: 'var(--text-body)',
            }}
          >
            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: tag.color, display: 'inline-block' }} />
            {tag.name}
            <button
              onClick={() => removeTag(tag.id)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--text-muted)', fontSize: '0.9rem', lineHeight: 1 }}
              title="Remove tag"
            >×</button>
          </span>
        ))}
        <button
          onClick={() => setShowAdd(s => !s)}
          className="btn btn-ghost"
          style={{ fontSize: '0.8rem', padding: '0.15rem 0.5rem' }}
        >
          + Tag
        </button>
      </div>

      {showAdd && (
        <div style={{ background: 'var(--cream-bg)', border: '1px solid var(--border)', borderRadius: '3px', padding: '0.75rem', marginTop: '0.25rem' }}>
          {available.length > 0 && (
            <div style={{ marginBottom: '0.5rem' }}>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.3rem' }}>Existing tags:</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
                {available.map(t => (
                  <button
                    key={t.id}
                    onClick={() => { addExisting(t); setShowAdd(false) }}
                    style={{
                      background: t.color + '22',
                      border: `1px solid ${t.color}66`,
                      borderRadius: '3px',
                      padding: '0.15rem 0.5rem',
                      fontSize: '0.8rem',
                      cursor: 'pointer',
                    }}
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <input
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="New tag name"
              style={{ flex: 1, minWidth: '120px' }}
              onKeyDown={e => { if (e.key === 'Enter') createAndAdd() }}
            />
            <div style={{ display: 'flex', gap: '0.25rem' }}>
              {TAG_COLORS.map(c => (
                <button
                  key={c}
                  onClick={() => setNewColor(c)}
                  style={{
                    width: '18px', height: '18px', borderRadius: '50%',
                    background: c, border: c === newColor ? '2px solid var(--navy)' : '1px solid transparent',
                    cursor: 'pointer', padding: 0,
                  }}
                />
              ))}
            </div>
            <button className="btn btn-primary" onClick={createAndAdd} disabled={adding || !newName.trim()}>
              {adding ? '…' : 'Add'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
