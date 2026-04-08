import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api/client'

function TimelineEvent({ event, onDocClick }) {
  const isKey = event.is_key_evidence
  const isTxn = event.type === 'transaction'

  return (
    <div style={{ display: 'flex', gap: '1.25rem', marginBottom: '1rem', position: 'relative' }}>
      {/* Date column */}
      <div style={{
        minWidth: '110px',
        textAlign: 'right',
        paddingTop: '2px',
        flexShrink: 0,
      }}>
        <span style={{
          fontSize: '0.82rem',
          fontWeight: 600,
          color: isTxn ? 'var(--gold-dark)' : 'var(--navy-light)',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {event.date?.split('T')[0] || ''}
        </span>
      </div>

      {/* Spine dot */}
      <div style={{
        width: '12px',
        height: '12px',
        borderRadius: '50%',
        background: isKey ? 'var(--rust)' : isTxn ? 'var(--gold)' : 'var(--navy-light)',
        border: '2px solid white',
        flexShrink: 0,
        marginTop: '3px',
        boxShadow: '0 0 0 2px ' + (isKey ? 'var(--rust)' : isTxn ? 'var(--gold-dark)' : 'var(--border)'),
        zIndex: 1,
      }} />

      {/* Content — <a> when linked so right-click "Open in New Tab" works */}
      <a
        href={event.doc_id ? (event.group_id ? `/groups/${event.group_id}` : `/documents/${event.doc_id}`) : undefined}
        onClick={(e) => {
          if (!event.doc_id) return
          if (e.ctrlKey || e.metaKey || e.shiftKey) return  // browser opens new tab
          e.preventDefault()
          onDocClick(event.doc_id, event.group_id)
        }}
        style={{
          flex:           1,
          background:     'var(--cream-card)',
          border:         `1px solid ${isKey ? 'var(--rust)' : 'var(--border)'}`,
          borderRadius:   '3px',
          padding:        '0.6rem 0.9rem',
          cursor:         event.doc_id ? 'pointer' : 'default',
          transition:     'box-shadow 0.15s',
          marginBottom:   '0.1rem',
          textDecoration: 'none',
          color:          'inherit',
          display:        'block',
        }}
        onMouseEnter={e => { if (event.doc_id) e.currentTarget.style.boxShadow = '0 2px 8px var(--shadow)' }}
        onMouseLeave={e => e.currentTarget.style.boxShadow = 'none'}
      >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem', justifyContent: 'space-between' }}>
          <div>
            {isKey && <span style={{ fontSize: '0.72rem', fontWeight: 700, color: 'var(--rust)', marginRight: '0.4rem', letterSpacing: '0.05em' }}>★ KEY EVIDENCE</span>}
            <span style={{ fontWeight: 600, fontSize: '0.92rem', color: 'var(--navy)' }}>
              {event.label || event.doc_title || 'Event'}
            </span>
          </div>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', flexShrink: 0, marginTop: '2px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {isTxn ? 'Transaction' : event.medium || 'Document'}
          </span>
        </div>

        {isTxn && (
          <div style={{ marginTop: '0.3rem', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            {event.seller && <span><em>From:</em> {event.seller}</span>}
            {event.seller && event.buyer && <span style={{ margin: '0 0.4rem' }}>→</span>}
            {event.buyer && <span><em>To:</em> {event.buyer}</span>}
            {event.price && <span style={{ marginLeft: '0.75rem' }}>{event.currency} {Number(event.price).toLocaleString()}</span>}
            {event.auction_house && <span style={{ marginLeft: '0.75rem' }}>· {event.auction_house}</span>}
          </div>
        )}

        {event.entity_names?.length > 0 && (
          <div style={{ marginTop: '0.3rem', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
            {event.entity_names.join(', ')}
          </div>
        )}

        {event.location && (
          <div style={{ fontSize: '0.8rem', color: 'var(--text-light)', marginTop: '0.15rem' }}>📍 {event.location}</div>
        )}
      </a>
    </div>
  )
}

export default function Timeline() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo]     = useState('')
  const [filterMode, setFilterMode] = useState('all')  // 'all' | 'transactions' | 'key'
  const navigate = useNavigate()

  const load = async () => {
    setLoading(true)
    try {
      const params = {}
      if (dateFrom) params.date_from = dateFrom
      if (dateTo)   params.date_to   = dateTo
      const res = await api.getTimeline(params)
      setData(res)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const events = data?.dated_events || []

  const filtered = events.filter(e => {
    if (filterMode === 'transactions') return e.type === 'transaction'
    if (filterMode === 'key') return e.is_key_evidence
    return true
  })

  // Group by year
  const byYear = {}
  for (const e of filtered) {
    const year = (e.date || '').substring(0, 4) || 'Unknown'
    if (!byYear[year]) byYear[year] = []
    byYear[year].push(e)
  }
  const years = Object.keys(byYear).sort()

  return (
    <div>
      <div className="page-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <h1>Provenance Timeline</h1>
            <div className="subtitle">
              {data ? `${data.total_dated} dated events, ${data.total_undated} undated` : 'Loading…'}
            </div>
          </div>
          <a
            href="/api/export/timeline"
            className="btn btn-ghost"
            target="_blank"
            rel="noreferrer"
          >
            ↓ Export PDF
          </a>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <div>
            <label style={{ display: 'inline', marginRight: '0.4rem' }}>From</label>
            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={{ width: 'auto', fontSize: '0.88rem' }} />
          </div>
          <div>
            <label style={{ display: 'inline', marginRight: '0.4rem' }}>To</label>
            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={{ width: 'auto', fontSize: '0.88rem' }} />
          </div>
          <button className="btn btn-primary" onClick={load}>Apply</button>

          <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
            {['all', 'transactions', 'key'].map(m => (
              <button
                key={m}
                className={`btn ${filterMode === m ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setFilterMode(m)}
                style={{ fontSize: '0.82rem', padding: '0.3rem 0.7rem' }}
              >
                {m === 'all' ? 'All' : m === 'transactions' ? 'Transactions' : '★ Key Only'}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ padding: '1.5rem 2rem' }}>
        {loading ? (
          <div className="loading">Building timeline…</div>
        ) : error ? (
          <div style={{ color: 'var(--rust)' }}>Error: {error}</div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            <h3>No timeline events yet</h3>
            <p>Ingest photos to populate the timeline.</p>
          </div>
        ) : (
          <div style={{ position: 'relative' }}>
            {/* Vertical spine */}
            <div style={{
              position: 'absolute',
              left: '138px',
              top: 0,
              bottom: 0,
              width: '2px',
              background: 'var(--border)',
              zIndex: 0,
            }} />

            {years.map(year => (
              <div key={year}>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '1.25rem',
                  marginBottom: '0.5rem',
                  marginTop: '1.5rem',
                }}>
                  <div style={{ width: '110px', textAlign: 'right' }}>
                    <span style={{
                      background: 'var(--navy)',
                      color: 'var(--cream-light)',
                      borderRadius: '3px',
                      padding: '1px 8px',
                      fontSize: '0.82rem',
                      fontWeight: 700,
                      letterSpacing: '0.03em',
                    }}>
                      {year}
                    </span>
                  </div>
                  <div style={{ width: '12px' }} />
                  <div style={{ flex: 1, height: '1px', background: 'var(--border)' }} />
                </div>
                {byYear[year].map((e, i) => (
                  <TimelineEvent
                    key={`${e.doc_id}-${e.type}-${i}`}
                    event={e}
                    onDocClick={(id, groupId) => navigate(groupId ? `/groups/${groupId}` : `/documents/${id}`)}
                  />
                ))}
              </div>
            ))}

            {/* Undated group */}
            {data?.undated_events?.length > 0 && filterMode === 'all' && (
              <div style={{ marginTop: '2rem' }}>
                <h3 style={{ color: 'var(--text-muted)', fontStyle: 'italic', fontSize: '0.95rem' }}>
                  Undated ({data.undated_events.length})
                </h3>
                {data.undated_events.map((e, i) => (
                  <TimelineEvent
                    key={`undated-${e.doc_id}-${i}`}
                    event={e}
                    onDocClick={(id, groupId) => navigate(groupId ? `/groups/${groupId}` : `/documents/${id}`)}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
