import React, { useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import api from '../api/client'
import { useJobStatus } from '../JobStatus'

const NAV = [
  { to: '/gallery',  icon: '▤', label: 'Gallery' },
  { to: '/search',   icon: '⌕', label: 'Search' },
  { to: '/timeline', icon: '↗', label: 'Timeline' },
  { to: '/network',  icon: '◉', label: 'Network' },
  { to: '/entities', icon: '⬡', label: 'Entities' },
  { to: '/trash',    icon: '🗑', label: 'Trash', dividerBefore: true },
]

const styles = {
  sidebar: {
    position: 'fixed',
    top: 0,
    left: 0,
    width: 'var(--sidebar-w)',
    height: '100vh',
    background: 'var(--navy-deep)',
    color: 'var(--cream-light)',
    display: 'flex',
    flexDirection: 'column',
    zIndex: 100,
    borderRight: '1px solid var(--navy-mid)',
    overflowY: 'auto',
  },
  brand: {
    padding: '1.5rem 1.25rem 1rem',
    borderBottom: '1px solid var(--navy-mid)',
  },
  brandTitle: {
    fontFamily: 'var(--font-serif)',
    fontSize: '1.05rem',
    fontWeight: 700,
    color: 'var(--gold)',
    letterSpacing: '0.02em',
    lineHeight: 1.3,
    margin: 0,
  },
  brandSubtitle: {
    fontSize: '0.72rem',
    color: 'var(--text-light)',
    marginTop: '0.25rem',
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
  },
  nav: {
    flex: 1,
    padding: '1rem 0',
  },
  navLink: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.6rem',
    padding: '0.6rem 1.25rem',
    color: 'rgba(250,247,242,0.65)',
    textDecoration: 'none',
    fontFamily: 'var(--font-serif)',
    fontSize: '0.95rem',
    transition: 'all 0.15s',
    borderLeft: '3px solid transparent',
  },
  navIcon: {
    fontSize: '1.1rem',
    width: '1.4rem',
    textAlign: 'center',
  },
  stats: {
    padding: '1rem 1.25rem',
    borderTop: '1px solid var(--navy-mid)',
    fontSize: '0.78rem',
    color: 'var(--text-light)',
  },
  statItem: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: '0.3rem',
  },
  statNumber: {
    color: 'var(--gold)',
    fontWeight: 600,
    fontVariantNumeric: 'tabular-nums',
  },
  ingestBtn: {
    display: 'block',
    width: '100%',
    padding: '0.55rem',
    background: 'var(--navy-mid)',
    color: 'var(--gold)',
    border: '1px solid var(--gold-dark)',
    borderRadius: '3px',
    fontFamily: 'var(--font-serif)',
    fontSize: '0.85rem',
    cursor: 'pointer',
    textAlign: 'center',
    marginTop: '0.75rem',
    transition: 'all 0.15s',
  },
}

export default function Sidebar({ stats }) {
  const [ingesting,       setIngesting]       = useState(false)
  const [ingestMsg,       setIngestMsg]       = useState('')
  const [archiveInput,    setArchiveInput]    = useState('')
  const [showArchive,     setShowArchive]     = useState(false)
  const [archiveSuggestions, setArchiveSuggestions] = useState([])
  const navigate = useNavigate()
  const { status: jobStatus } = useJobStatus()

  // Load existing archive names for autocomplete once
  React.useEffect(() => {
    api.getArchives().then(r => setArchiveSuggestions(r.archives || [])).catch(() => {})
  }, [])

  const handleIngest = async () => {
    const archive = archiveInput.trim()
    if (!archive) {
      setShowArchive(true)
      setIngestMsg('Source is required before ingesting.')
      return
    }
    try {
      setIngesting(true)
      setIngestMsg('Starting…')
      const res = await api.startIngest(archive)
      setIngestMsg(res.message || 'Running…')

      // Listen to SSE progress
      const evtSrc = new EventSource('/api/ingest/progress')
      evtSrc.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          if (data.type === 'done') {
            setIngestMsg(`Done: ${data.processed} processed`)
            setIngesting(false)
            evtSrc.close()
          } else if (data.type === 'done_file') {
            setIngestMsg(`${data.completed}/${data.total}: ${data.file}`)
          } else if (data.type === 'processing') {
            setIngestMsg(`Processing ${data.file}…`)
          } else if (data.type === 'error') {
            setIngestMsg(`Error: ${data.message}`)
          }
        } catch {}
      }
      evtSrc.onerror = () => {
        setIngesting(false)
        evtSrc.close()
      }
    } catch (err) {
      setIngestMsg(err.message)
      setIngesting(false)
    }
  }

  const activeStyle = {
    ...styles.navLink,
    color: 'var(--cream-light)',
    borderLeftColor: 'var(--gold)',
    background: 'rgba(201,168,76,0.08)',
  }

  return (
    <aside style={styles.sidebar}>
      <div style={styles.brand}>
        <div style={styles.brandTitle}>Provenance Archive Wiki</div>
        <div style={styles.brandSubtitle}>Research Archive</div>
      </div>

      <nav style={styles.nav}>
        {NAV.map(({ to, icon, label, dividerBefore }) => (
          <React.Fragment key={to}>
            {dividerBefore && (
              <div style={{ height: '1px', background: 'var(--navy-mid)', margin: '0.5rem 1.25rem' }} />
            )}
            <NavLink
              to={to}
              style={({ isActive }) => isActive ? activeStyle : styles.navLink}
            >
              <span style={styles.navIcon}>{icon}</span>
              {label}
            </NavLink>
          </React.Fragment>
        ))}

      </nav>

      {stats && (
        <div style={styles.stats}>
          <div style={{ fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-light)', textTransform: 'uppercase', fontSize: '0.7rem', letterSpacing: '0.08em' }}>
            Archive
          </div>
          {[
            ['Documents', stats.documents],
            ['Entities', stats.entities],
            ['Transactions', stats.transactions],
            ['Tags', stats.tags],
            ['Key Evidence', stats.key_evidence],
          ].map(([label, val]) => (
            <div key={label} style={styles.statItem}>
              <span>{label}</span>
              <span style={styles.statNumber}>{val ?? '–'}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ padding: '0 1rem 1.5rem' }}>

        {/* Archive label toggle */}
        <button
          onClick={() => setShowArchive(v => !v)}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            fontSize: '0.72rem',
            color: archiveInput.trim() ? 'var(--gold)' : 'var(--text-light)',
            width: '100%',
            textAlign: 'left',
            padding: '0 0 0.3rem',
            fontFamily: 'var(--font-serif)',
            letterSpacing: '0.03em',
          }}
        >
          {showArchive ? '▾' : '▸'} Source{archiveInput.trim() ? `: ${archiveInput.trim()}` : ' (required)'}
        </button>

        {/* Collapsible input + datalist */}
        {showArchive && (
          <div style={{ marginBottom: '0.5rem' }}>
            <input
              list="archive-suggestions"
              value={archiveInput}
              onChange={e => setArchiveInput(e.target.value)}
              placeholder="e.g. Rijksmuseum, Archive A…"
              style={{
                width: '100%',
                boxSizing: 'border-box',
                fontSize: '0.82rem',
                background: 'var(--navy-mid)',
                border: '1px solid var(--navy-light)',
                borderRadius: '3px',
                color: 'var(--cream-light)',
                padding: '0.35rem 0.6rem',
                fontFamily: 'var(--font-serif)',
              }}
            />
            <datalist id="archive-suggestions">
              {archiveSuggestions.map(a => <option key={a} value={a} />)}
            </datalist>
            {archiveInput.trim() && (
              <button
                onClick={() => setArchiveInput('')}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.72rem', color: 'var(--text-light)', padding: '0.2rem 0', fontFamily: 'inherit' }}
              >
                ✕ Clear
              </button>
            )}
          </div>
        )}

        <button
          style={{
            ...styles.ingestBtn,
            opacity: ingesting ? 0.7 : 1,
            cursor: ingesting ? 'not-allowed' : 'pointer',
          }}
          onClick={handleIngest}
          disabled={ingesting}
        >
          {ingesting ? '⟳ Ingesting…' : '⊕ Ingest Photos'}
        </button>
        {ingestMsg && (
          <div style={{ marginTop: '0.4rem', fontSize: '0.72rem', color: 'var(--text-light)', textAlign: 'center', lineBreak: 'anywhere' }}>
            {ingestMsg}
          </div>
        )}

        {jobStatus.message && (
          <div style={{
            marginTop: '0.6rem',
            padding: '0.4rem 0.5rem',
            background: 'var(--navy-mid)',
            border: '1px solid var(--navy-light)',
            borderRadius: '3px',
            fontSize: '0.72rem',
            color: jobStatus.busy ? 'var(--gold)' : 'var(--cream-light)',
            textAlign: 'center',
            lineBreak: 'anywhere',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '0.4rem',
          }}>
            {jobStatus.busy && <span style={{ display: 'inline-block', animation: 'spin 1s linear infinite' }}>⟳</span>}
            <span>{jobStatus.message}</span>
          </div>
        )}
      </div>
    </aside>
  )
}
