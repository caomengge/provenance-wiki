import React, { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import api from '../api/client'

const STATUS_TABS = [
  { key: 'ok',       label: 'Ingested' },
  { key: 'err',      label: 'Failed' },
  { key: 'skipped',  label: 'Skipped' },
  { key: 'requeued', label: 'Re-queued' },
]

function formatTs(ts) {
  if (!ts) return '–'
  // SQLite datetime('now') returns 'YYYY-MM-DD HH:MM:SS' in UTC
  const d = new Date(ts.replace(' ', 'T') + 'Z')
  return isNaN(d) ? ts : d.toLocaleString()
}

function statusBadgeColor(status) {
  if (status === 'done')    return 'var(--gold)'
  if (status === 'running') return '#7ab8ff'
  if (status === 'crashed') return '#e26d6d'
  return 'var(--text-light)'
}

export default function IngestLog() {
  const [runs,        setRuns]        = useState([])
  const [selectedId,  setSelectedId]  = useState(null)
  const [run,         setRun]         = useState(null)
  const [counts,      setCounts]      = useState({})
  const [activeTab,   setActiveTab]   = useState('ok')
  const [files,       setFiles]       = useState([])
  const [loadingRun,  setLoadingRun]  = useState(false)
  const [loadingFiles,setLoadingFiles]= useState(false)

  const loadRuns = useCallback(async () => {
    try {
      const res = await api.getIngestRuns(50)
      setRuns(res.runs || [])
      if (res.runs?.length && selectedId == null) {
        setSelectedId(res.runs[0].id)
      }
    } catch (err) {
      console.error(err)
    }
  }, [selectedId])

  useEffect(() => { loadRuns() }, [loadRuns])

  // Auto-refresh while a run is in progress
  useEffect(() => {
    const hasRunning = runs.some(r => r.status === 'running')
    if (!hasRunning) return
    const t = setInterval(loadRuns, 3000)
    return () => clearInterval(t)
  }, [runs, loadRuns])

  useEffect(() => {
    if (selectedId == null) return
    setLoadingRun(true)
    api.getIngestRun(selectedId)
       .then(res => { setRun(res.run); setCounts(res.counts || {}) })
       .catch(err => console.error(err))
       .finally(() => setLoadingRun(false))
  }, [selectedId])

  useEffect(() => {
    if (selectedId == null) return
    setLoadingFiles(true)
    api.getIngestRunFiles(selectedId, activeTab)
       .then(res => setFiles(res.files || []))
       .catch(err => console.error(err))
       .finally(() => setLoadingFiles(false))
  }, [selectedId, activeTab])

  return (
    <div style={{ display: 'flex', height: '100%', gap: '1rem' }}>
      {/* Left pane: run list */}
      <div style={{ width: 280, borderRight: '1px solid var(--border)', paddingRight: '0.75rem', overflowY: 'auto' }}>
        <div className="page-header" style={{ marginBottom: '0.5rem' }}>
          <h1 style={{ fontSize: '1.1rem', margin: 0 }}>Ingest Log</h1>
        </div>
        {runs.length === 0 && (
          <div style={{ color: 'var(--text-light)', fontSize: '0.85rem', padding: '0.5rem' }}>
            No ingestion runs recorded yet.
          </div>
        )}
        {runs.map(r => {
          const isActive = r.id === selectedId
          return (
            <button
              key={r.id}
              onClick={() => setSelectedId(r.id)}
              style={{
                display: 'block',
                width: '100%',
                textAlign: 'left',
                padding: '0.5rem 0.6rem',
                marginBottom: '0.3rem',
                background: isActive ? 'rgba(201,168,76,0.12)' : 'transparent',
                border: '1px solid ' + (isActive ? 'var(--gold-dark)' : 'var(--border)'),
                borderRadius: '3px',
                cursor: 'pointer',
                fontFamily: 'inherit',
                fontSize: '0.82rem',
                color: 'var(--text-dark)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontWeight: 600 }}>#{r.id}</span>
                <span style={{ fontSize: '0.72rem', color: statusBadgeColor(r.status) }}>
                  {r.status}
                </span>
              </div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-light)', marginTop: '0.15rem' }}>
                {formatTs(r.started_at)}
              </div>
              <div style={{ fontSize: '0.72rem', marginTop: '0.2rem' }}>
                <span style={{ color: 'var(--gold)' }}>{r.processed}</span>
                {' / '}
                <span style={{ color: 'var(--text-light)' }}>{r.skipped} skip</span>
                {' / '}
                <span style={{ color: r.errors ? '#e26d6d' : 'var(--text-light)' }}>{r.errors} err</span>
              </div>
              {r.source_archive && (
                <div style={{ fontSize: '0.7rem', color: 'var(--text-light)', marginTop: '0.15rem', fontStyle: 'italic' }}>
                  {r.source_archive}
                </div>
              )}
            </button>
          )
        })}
      </div>

      {/* Right pane: run detail */}
      <div style={{ flex: 1, overflowY: 'auto', minWidth: 0 }}>
        {!selectedId && (
          <div style={{ color: 'var(--text-light)', padding: '1rem' }}>Select a run to view details.</div>
        )}
        {selectedId && run && (
          <>
            <div className="page-header">
              <h1 style={{ margin: 0 }}>Run #{run.id}</h1>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-light)', marginTop: '0.25rem' }}>
                {run.source_archive ? `${run.source_archive} · ` : ''}
                started {formatTs(run.started_at)}
                {run.finished_at && ` · finished ${formatTs(run.finished_at)}`}
                {' · '}<span style={{ color: statusBadgeColor(run.status) }}>{run.status}</span>
              </div>
              <div style={{ fontSize: '0.85rem', marginTop: '0.4rem' }}>
                Total {run.total} ·{' '}
                <span style={{ color: 'var(--gold)' }}>{run.processed} processed</span> ·{' '}
                <span>{run.skipped} skipped</span> ·{' '}
                <span style={{ color: run.errors ? '#e26d6d' : undefined }}>{run.errors} errors</span>
              </div>
            </div>

            {/* Tabs */}
            <div style={{ display: 'flex', gap: '0.25rem', borderBottom: '1px solid var(--border)', marginBottom: '0.5rem' }}>
              {STATUS_TABS.map(tab => {
                const n = counts[tab.key] || 0
                const isActive = activeTab === tab.key
                return (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    style={{
                      padding: '0.5rem 0.9rem',
                      background: 'none',
                      border: 'none',
                      borderBottom: '2px solid ' + (isActive ? 'var(--gold)' : 'transparent'),
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                      fontSize: '0.9rem',
                      color: isActive ? 'var(--text-dark)' : 'var(--text-light)',
                      fontWeight: isActive ? 600 : 400,
                    }}
                  >
                    {tab.label} <span style={{ color: 'var(--text-light)' }}>({n})</span>
                  </button>
                )
              })}
            </div>

            {/* File list */}
            {loadingFiles && <div style={{ color: 'var(--text-light)' }}>Loading…</div>}
            {!loadingFiles && files.length === 0 && (
              <div style={{ color: 'var(--text-light)', padding: '0.5rem' }}>No files in this category.</div>
            )}
            {!loadingFiles && files.length > 0 && (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: 'var(--text-light)' }}>
                    <th style={{ padding: '0.4rem 0.5rem', borderBottom: '1px solid var(--border)' }}>File</th>
                    <th style={{ padding: '0.4rem 0.5rem', borderBottom: '1px solid var(--border)' }}>Document</th>
                    {activeTab === 'err' && (
                      <th style={{ padding: '0.4rem 0.5rem', borderBottom: '1px solid var(--border)' }}>Error</th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {files.map(f => (
                    <tr key={f.sha256} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '0.4rem 0.5rem', wordBreak: 'break-all' }}>{f.filename}</td>
                      <td style={{ padding: '0.4rem 0.5rem' }}>
                        {f.document_id
                          ? <Link to={`/documents/${f.document_id}`}>{f.document_title || `#${f.document_id}`}</Link>
                          : <span style={{ color: 'var(--text-light)' }}>–</span>}
                      </td>
                      {activeTab === 'err' && (
                        <td style={{ padding: '0.4rem 0.5rem', color: '#e26d6d', fontFamily: 'monospace', fontSize: '0.78rem' }}>
                          {f.error_message}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
        {selectedId && !run && !loadingRun && (
          <div style={{ color: 'var(--text-light)' }}>Run not found.</div>
        )}
      </div>
    </div>
  )
}
