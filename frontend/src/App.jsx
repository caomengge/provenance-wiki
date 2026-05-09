import React, { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
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
import QAPanel from './components/QAPanel'
import { JobStatusProvider } from './JobStatus'
import api from './api/client'

export default function App() {
  const [stats, setStats] = useState(null)
  const [qaOpen, setQaOpen] = useState(false)

  useEffect(() => {
    api.getStats().then(setStats).catch(() => {})
  }, [])

  return (
    <BrowserRouter>
      <JobStatusProvider>
      <div className="app-layout">
        <Sidebar stats={stats} />
        <QAPanel isOpen={qaOpen} onClose={() => setQaOpen(false)} />
        <main className="main-content">
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
