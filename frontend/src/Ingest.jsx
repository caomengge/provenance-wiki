import React, { createContext, useCallback, useContext, useRef, useState } from 'react'
import api from './api/client'

/**
 * Shared ingestion state, so the sidebar button and Settings → "4. Add your
 * photos" show the same run, progress and Source.
 *
 * useIngest() → { ingesting, message, lastRun, source, setSource, start(source) }
 * The last Source used is remembered in this browser (a convenience only).
 */
const IngestContext = createContext(null)
const SOURCE_KEY = 'pw-last-source'

function readSource() {
  try { return localStorage.getItem(SOURCE_KEY) || '' } catch { return '' }
}

export function IngestProvider({ children }) {
  const [ingesting, setIngesting] = useState(false)
  const [message, setMessage] = useState('')
  const [lastRun, setLastRun] = useState(null)        // { processed } when a run finishes
  const [source, setSourceState] = useState(readSource)
  const stream = useRef(null)

  const setSource = useCallback((value) => {
    setSourceState(value)
    try { localStorage.setItem(SOURCE_KEY, value) } catch { /* storage unavailable */ }
  }, [])

  const start = useCallback(async (requested) => {
    const src = (requested ?? source).trim()
    if (!src) {
      setMessage('Enter the Source (where these documents come from) first.')
      return false
    }
    setSource(src)
    setIngesting(true); setLastRun(null); setMessage('Starting…')
    try {
      const res = await api.startIngest(src)
      setMessage(res.message || 'Running…')
      if (stream.current) stream.current.close()
      const evt = new EventSource('/api/ingest/progress')
      stream.current = evt
      evt.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          if (data.type === 'done') {
            setMessage(`Done: ${data.processed} processed`)
            setLastRun({ processed: data.processed })
            setIngesting(false)
            evt.close()
          } else if (data.type === 'done_file') {
            setMessage(`${data.completed}/${data.total}: ${data.file}`)
          } else if (data.type === 'processing') {
            setMessage(`Processing ${data.file}…`)
          } else if (data.type === 'scan') {
            setMessage(data.message)
          } else if (data.type === 'error') {
            setMessage(`Error: ${data.message}`)
          }
        } catch { /* ignore malformed events */ }
      }
      evt.onerror = () => { setIngesting(false); evt.close() }
      return true
    } catch (err) {
      setMessage(err.message)
      setIngesting(false)
      return false
    }
  }, [source, setSource])

  return (
    <IngestContext.Provider value={{ ingesting, message, lastRun, source, setSource, start, setMessage }}>
      {children}
    </IngestContext.Provider>
  )
}

export function useIngest() {
  const ctx = useContext(IngestContext)
  if (!ctx) throw new Error('useIngest must be used inside <IngestProvider>')
  return ctx
}
