import React, { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, Link, useNavigate, useLocation } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import Gallery from './views/Gallery'
import DocumentDetail from './views/DocumentDetail'
import Timeline from './views/Timeline'
import NetworkGraph from './views/NetworkGraph'
import Search from './views/Search'
import QA from './views/QA'
import Trash from './views/Trash'
import Entities from './views/Entities'
import GroupDetail from './views/GroupDetail'
import IngestLog from './views/IngestLog'
import Settings from './views/Settings'
import QAPanel from './components/QAPanel'
import { JobStatusProvider } from './JobStatus'
import api from './api/client'

// First launch of the desktop app with no keys at all: open the setup screen
// once per browser session. Researchers can skip it and browse.
function FirstRunRedirect({ health }) {
  const navigate = useNavigate()
  const location = useLocation()
  useEffect(() => {
    if (!health || !health.packaged) return
    if (health.anthropic_key_set || health.voyage_key_set) return
    let shown = false
    try { shown = sessionStorage.getItem('pw-setup-shown') === '1' } catch { /* storage unavailable */ }
    if (shown || location.pathname === '/settings') return
    try { sessionStorage.setItem('pw-setup-shown', '1') } catch { /* ignore */ }
    navigate('/settings?welcome=1')
  }, [health]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

const bannerStyle = {
  padding: '0.45rem 1rem', fontSize: '0.82rem', borderBottom: '1px solid', lineHeight: 1.45,
}

function Banners({ health }) {
  const location = useLocation()
  if (!health) return null
  const missing = []
  if (!health.anthropic_key_set) missing.push('Anthropic (reading documents, answering questions)')
  if (!health.voyage_key_set) missing.push('Voyage AI (semantic search)')
  return (
    <>
      {health.testing_build && (
        <div style={{ ...bannerStyle, background: '#fef3c7', borderColor: '#f59e0b', color: '#78350f' }}>
          <strong>Testing build</strong>: not signed or notarized for public distribution. Please use test material or copies of your documents.
        </div>
      )}
      {missing.length > 0 && location.pathname !== '/settings' && (
        <div style={{ ...bannerStyle, background: '#eff6ff', borderColor: '#93c5fd', color: '#1e3a8a' }}>
          <strong>Setup needed:</strong> add your API key{missing.length > 1 ? 's' : ''} for {missing.join(' and ')} in <Link to="/settings">Settings</Link>.
        </div>
      )}
    </>
  )
}

export default function App() {
  const [stats, setStats] = useState(null)
  const [qaOpen, setQaOpen] = useState(false)

  const [health, setHealth] = useState(null)
  const refreshHealth = () => api.getHealth().then(setHealth).catch(() => {})

  useEffect(() => {
    api.getStats().then(setStats).catch(() => {})
    refreshHealth()
  }, [])

  return (
    <BrowserRouter>
      <JobStatusProvider>
      <div className="app-layout">
        <Sidebar stats={stats} />
        <QAPanel isOpen={qaOpen} onClose={() => setQaOpen(false)} />
        <main className="main-content">
          <FirstRunRedirect health={health} />
          <Banners health={health} />
          <Routes>
            <Route path="/"            element={<Navigate to="/gallery" replace />} />
            <Route path="/gallery"     element={<Gallery onStatsUpdate={setStats} />} />
            <Route path="/documents/:id" element={<DocumentDetail />} />
            <Route path="/timeline"    element={<Timeline />} />
            <Route path="/network"     element={<NetworkGraph />} />
            <Route path="/search"      element={<Search />} />
            <Route path="/qa"          element={<QA />} />
            <Route path="/entities"    element={<Entities />} />
            <Route path="/trash"       element={<Trash />} />
            <Route path="/groups/:id"  element={<GroupDetail />} />
            <Route path="/ingest-log"  element={<IngestLog />} />
            <Route path="/settings"    element={<Settings onSettingsChanged={refreshHealth} />} />
          </Routes>
        </main>

        {/* Floating QA button */}
        <button
          onClick={() => setQaOpen(true)}
          title="Ask a Question"
          style={{
            position: 'fixed',
            bottom: '1.75rem',
            right: '1.75rem',
            width: '52px',
            height: '52px',
            borderRadius: '50%',
            background: 'var(--gold)',
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '1.4rem',
            boxShadow: '0 4px 16px rgba(0,0,0,0.25)',
            zIndex: 200,
            transition: 'transform 0.15s, box-shadow 0.15s',
          }}
          onMouseEnter={e => { e.currentTarget.style.transform = 'scale(1.08)'; e.currentTarget.style.boxShadow = '0 6px 20px rgba(0,0,0,0.32)' }}
          onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)';    e.currentTarget.style.boxShadow = '0 4px 16px rgba(0,0,0,0.25)' }}
        >
          💬
        </button>
      </div>
      </JobStatusProvider>
    </BrowserRouter>
  )
}
