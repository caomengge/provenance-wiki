import React, { createContext, useContext, useState, useCallback, useRef } from 'react'

/**
 * Lightweight global "what's happening" channel so components like the gallery
 * (group create) and the group detail (re-extract) can publish status to the
 * sidebar — same UX as ingestion progress, no SSE infrastructure required.
 *
 * setStatus({ message, busy }) — busy=true while running, false on done.
 * runJob(label, fn) — convenience wrapper: shows label, runs fn, then
 *   shows "Done" or the error and auto-clears after a few seconds.
 */
const JobStatusContext = createContext(null)

export function JobStatusProvider({ children }) {
  const [status, setStatus] = useState({ message: '', busy: false })
  const clearTimer = useRef(null)

  const update = useCallback((next) => {
    if (clearTimer.current) {
      clearTimeout(clearTimer.current)
      clearTimer.current = null
    }
    setStatus(next)
  }, [])

  const runJob = useCallback(async (label, fn) => {
    update({ message: label, busy: true })
    try {
      const result = await fn()
      update({ message: `Done: ${label}`, busy: false })
      clearTimer.current = setTimeout(() => setStatus({ message: '', busy: false }), 5000)
      return result
    } catch (err) {
      update({ message: `Failed: ${err?.message || label}`, busy: false })
      clearTimer.current = setTimeout(() => setStatus({ message: '', busy: false }), 8000)
      throw err
    }
  }, [update])

  return (
    <JobStatusContext.Provider value={{ status, setStatus: update, runJob }}>
      {children}
    </JobStatusContext.Provider>
  )
}

export function useJobStatus() {
  const ctx = useContext(JobStatusContext)
  if (!ctx) throw new Error('useJobStatus must be used inside <JobStatusProvider>')
  return ctx
}
