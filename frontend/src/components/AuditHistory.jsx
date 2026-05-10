import React, { useEffect, useState } from 'react'
import api from '../api/client'

function formatTs(ts) {
  if (!ts) return '–'
  const d = new Date(ts.replace(' ', 'T') + 'Z')
  return isNaN(d) ? ts : d.toLocaleString()
}

function summarize(ev) {
  if (ev.action === 'update' && ev.field) {
    return (
      <>
        Updated <code>{ev.field}</code>:{' '}
        <span style={{ color: 'var(--text-light)' }}>{ev.old_value || '∅'}</span>
        {' → '}
        <span>{ev.new_value || '∅'}</span>
      </>
    )
  }
  if (ev.action === 'create')        return <>Created <code>{ev.new_value}</code></>
  if (ev.action === 'delete')        return <>Deleted <code>{ev.old_value}</code></>
  if (ev.action === 'add_entity')    return <>Added entity <code>{ev.new_value}</code></>
  if (ev.action === 'remove_entity') return <>Removed entity <code>{ev.old_value}</code></>
  if (ev.action === 'link')          return <>Linked: <code>{ev.new_value}</code></>
  if (ev.action === 'unlink')        return <>Unlinked: <code>{ev.old_value}</code></>
  if (ev.action === 'wipe')          return <>Wiped extracted metadata</>
  if (ev.action === 'join_group')    return <>Joined group: <code>{ev.new_value}</code></>
  return <>{ev.action}</>
}

export default function AuditHistory({ entityType, entityId }) {
  const [events,  setEvents]  = useState([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')

  useEffect(() => {
    if (!entityType || entityId == null) return
    setLoading(true)
    api.getAudit({ entityType, entityId, limit: 200 })
      .then(res => setEvents(res.events || []))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [entityType, entityId])

  if (loading) return <div style={{ color: 'var(--text-light)' }}>Loading history…</div>
  if (error)   return <div style={{ color: '#e26d6d' }}>{error}</div>
  if (events.length === 0) return <div style={{ color: 'var(--text-light)', fontStyle: 'italic' }}>No edits recorded yet.</div>

  return (
    <ol style={{ listStyle: 'none', padding: 0, margin: 0 }}>
      {events.map(ev => (
        <li key={ev.id} style={{
          padding: '0.5rem 0',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.85rem',
        }}>
          <div style={{ color: 'var(--text-light)', fontSize: '0.75rem', marginBottom: '0.15rem' }}>
            {formatTs(ev.ts)} · {ev.actor}
            {ev.run_id && <> · run #{ev.run_id}</>}
          </div>
          <div style={{ wordBreak: 'break-word' }}>{summarize(ev)}</div>
        </li>
      ))}
    </ol>
  )
}
