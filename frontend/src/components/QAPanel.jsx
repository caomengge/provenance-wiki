/**
 * QAPanel.jsx — Slide-in Research Assistant chat panel.
 *
 * Mounts once in App.jsx and stays in the DOM so conversation history
 * persists as the user navigates between pages.
 *
 * Z-index stack:
 *   Sidebar          100
 *   QAPanel backdrop 150
 *   QAPanel          151
 *   DocPreviewPanel  200 / 201  (appears on top when a citation is clicked)
 */

import React, { useState, useRef, useEffect } from 'react'
import api from '../api/client'
import DocPreviewPanel from './DocPreviewPanel'

const EXAMPLE_QUESTIONS = [
  'Who owned this artwork before World War II?',
  'What is the complete ownership chain for this piece?',
  'Were any works sold at auction in Germany during the 1930s?',
  'Which dealers or auction houses appear most frequently?',
  'Are there any gaps in the provenance record?',
]

const CONFIDENCE_COLORS = {
  high:   { bg: '#d1fae5', color: '#065f46', label: 'High confidence' },
  medium: { bg: '#fef3c7', color: '#78350f', label: 'Medium confidence' },
  low:    { bg: '#fee2e2', color: '#7f1d1d', label: 'Low confidence' },
  none:   { bg: '#f3f4f6', color: '#4b5563', label: 'Not found in archive' },
}

export default function QAPanel({ isOpen, onClose }) {
  const [history, setHistory]       = useState([])
  const [question, setQuestion]     = useState('')
  const [loading, setLoading]       = useState(false)
  const [panelDocId, setPanelDocId] = useState(null)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  // Focus the input whenever the panel opens
  useEffect(() => {
    if (isOpen) setTimeout(() => inputRef.current?.focus(), 300)
  }, [isOpen])

  // Auto-scroll chat to the latest message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history])

  // Escape key closes the panel (unless a doc-preview is on top)
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'Escape' && isOpen && !panelDocId) onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isOpen, panelDocId, onClose])

  const ask = async () => {
    const q = question.trim()
    if (!q || loading) return
    setQuestion('')
    setLoading(true)
    setHistory(h => [...h, { role: 'user', text: q }])
    try {
      const res = await api.askQuestion(q)
      setHistory(h => [...h, { role: 'assistant', ...res }])
    } catch (err) {
      setHistory(h => [...h, { role: 'assistant', answer: `Error: ${err.message}`, confidence: 'none', citations: [] }])
    } finally {
      setLoading(false)
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask() }
  }

  return (
    <>
      {/* Backdrop — rendered only when open so it doesn't block interaction */}
      {isOpen && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed',
            inset:    0,
            background: 'rgba(15,25,35,0.3)',
            zIndex:   150,
          }}
        />
      )}

      {/* Sliding panel — always mounted, visibility controlled by transform */}
      <div style={{
        position:      'fixed',
        top:           0,
        right:         0,
        width:         '460px',
        height:        '100vh',
        background:    'var(--cream-light)',
        borderLeft:    '1px solid var(--border)',
        boxShadow:     '-4px 0 24px rgba(0,0,0,0.18)',
        zIndex:        151,
        display:       'flex',
        flexDirection: 'column',
        transform:     isOpen ? 'translateX(0)' : 'translateX(100%)',
        transition:    'transform 0.28s ease',
        pointerEvents: isOpen ? 'auto' : 'none',
      }}>

        {/* ── Header ──────────────────────────────────────────────────────── */}
        <div style={{
          display:        'flex',
          alignItems:     'center',
          justifyContent: 'space-between',
          padding:        '0.85rem 1.25rem',
          borderBottom:   '1px solid var(--border)',
          background:     'var(--navy)',
          flexShrink:     0,
        }}>
          <div>
            <div style={{
              fontWeight:  700,
              fontSize:    '0.95rem',
              color:       'var(--cream-light)',
              fontFamily:  'var(--font-serif)',
            }}>
              Research Assistant
            </div>
            <div style={{ fontSize: '0.72rem', color: 'rgba(250,247,242,0.5)', marginTop: '1px' }}>
              Ask questions about your archive
            </div>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            {history.length > 0 && (
              <button
                onClick={() => setHistory([])}
                style={{
                  fontSize:    '0.75rem',
                  color:       'rgba(250,247,242,0.6)',
                  background:  'none',
                  border:      '1px solid rgba(250,247,242,0.2)',
                  borderRadius: '3px',
                  padding:     '0.15rem 0.55rem',
                  cursor:      'pointer',
                  fontFamily:  'var(--font-serif)',
                }}
              >
                Clear
              </button>
            )}
            <button
              onClick={onClose}
              aria-label="Close assistant"
              style={{
                background: 'none',
                border:     'none',
                cursor:     'pointer',
                fontSize:   '1.4rem',
                color:      'rgba(250,247,242,0.65)',
                lineHeight: 1,
                padding:    '0 2px',
              }}
            >×</button>
          </div>
        </div>

        {/* ── Chat area ────────────────────────────────────────────────────── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '1rem 1.25rem' }}>
          {history.length === 0 ? (
            /* Empty state — instructions + example questions */
            <div>
              <div style={{
                background:   'var(--cream-card)',
                border:       '1px solid var(--border)',
                borderRadius: '4px',
                padding:      '1rem',
                marginBottom: '1rem',
              }}>
                <p style={{ color: 'var(--text-muted)', fontSize: '0.88rem', margin: 0, lineHeight: 1.65 }}>
                  Ask any provenance research question. The assistant searches your archive and gives answers grounded in your documents.
                </p>
              </div>

              <div style={{
                fontSize:       '0.75rem',
                fontWeight:     700,
                textTransform:  'uppercase',
                letterSpacing:  '0.06em',
                color:          'var(--text-muted)',
                marginBottom:   '0.5rem',
              }}>
                Example questions
              </div>

              {EXAMPLE_QUESTIONS.map((q, i) => (
                <button
                  key={i}
                  onClick={() => { setQuestion(q); inputRef.current?.focus() }}
                  style={{
                    display:      'block',
                    width:        '100%',
                    textAlign:    'left',
                    background:   'var(--cream-card)',
                    border:       '1px solid var(--border)',
                    borderRadius: '3px',
                    padding:      '0.5rem 0.75rem',
                    marginBottom: '0.35rem',
                    cursor:       'pointer',
                    fontFamily:   'var(--font-serif)',
                    fontSize:     '0.87rem',
                    color:        'var(--navy)',
                    transition:   'background 0.15s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--cream-bg)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'var(--cream-card)'}
                >
                  {q}
                </button>
              ))}
            </div>
          ) : (
            /* Conversation history */
            <div>
              {history.map((entry, i) => (
                <div key={i} style={{ marginBottom: '1.25rem' }}>
                  {entry.role === 'user' ? (
                    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                      <div style={{
                        background:   'var(--navy)',
                        color:        'var(--cream-light)',
                        borderRadius: '12px 12px 3px 12px',
                        padding:      '0.6rem 0.9rem',
                        maxWidth:     '82%',
                        fontFamily:   'var(--font-serif)',
                        fontSize:     '0.9rem',
                        lineHeight:   1.6,
                      }}>
                        {entry.text}
                      </div>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'flex-start' }}>
                      {/* Avatar */}
                      <div style={{
                        width:          '28px',
                        height:         '28px',
                        borderRadius:   '50%',
                        background:     'var(--gold)',
                        display:        'flex',
                        alignItems:     'center',
                        justifyContent: 'center',
                        fontSize:       '0.8rem',
                        fontWeight:     700,
                        color:          'var(--navy)',
                        flexShrink:     0,
                        marginTop:      '2px',
                      }}>A</div>

                      <div style={{ flex: 1, minWidth: 0 }}>
                        {/* Confidence badge */}
                        {entry.confidence && (
                          <div style={{ marginBottom: '0.4rem' }}>
                            <span style={{
                              ...CONFIDENCE_COLORS[entry.confidence],
                              display:      'inline-block',
                              padding:      '1px 8px',
                              borderRadius: '10px',
                              fontSize:     '0.72rem',
                              fontWeight:   600,
                            }}>
                              {CONFIDENCE_COLORS[entry.confidence].label}
                            </span>
                          </div>
                        )}

                        {/* Answer bubble */}
                        <div style={{
                          background:   'var(--cream-card)',
                          border:       '1px solid var(--border)',
                          borderRadius: '3px 12px 12px 12px',
                          padding:      '0.75rem 1rem',
                          lineHeight:   1.75,
                          fontSize:     '0.9rem',
                          whiteSpace:   'pre-wrap',
                        }}>
                          {entry.answer}
                        </div>

                        {/* Citation buttons */}
                        {entry.citations?.length > 0 && (
                          <div style={{ marginTop: '0.5rem' }}>
                            <div style={{
                              fontSize:      '0.72rem',
                              fontWeight:    700,
                              textTransform: 'uppercase',
                              letterSpacing: '0.06em',
                              color:         'var(--text-muted)',
                              marginBottom:  '0.3rem',
                            }}>
                              Sources
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
                              {entry.citations.map(c => (
                                <button
                                  key={c.doc_id}
                                  onClick={() => setPanelDocId(c.doc_id)}
                                  style={{
                                    background:   'var(--cream-bg)',
                                    border:       '1px solid var(--border)',
                                    borderRadius: '3px',
                                    padding:      '0.15rem 0.5rem',
                                    fontSize:     '0.78rem',
                                    color:        'var(--navy)',
                                    cursor:       'pointer',
                                    fontFamily:   'inherit',
                                  }}
                                >
                                  Doc #{c.doc_id}: {c.title?.substring(0, 25)}{c.title?.length > 25 ? '…' : ''}
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}

              {loading && (
                <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center', color: 'var(--text-muted)', fontSize: '0.87rem', fontStyle: 'italic' }}>
                  <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: 'var(--gold)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.8rem', fontWeight: 700, color: 'var(--navy)', flexShrink: 0 }}>A</div>
                  Searching archive and composing answer…
                </div>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* ── Input area ───────────────────────────────────────────────────── */}
        <div style={{
          borderTop:  '1px solid var(--border)',
          background: 'var(--cream-card)',
          padding:    '0.75rem 1.25rem',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <textarea
              ref={inputRef}
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
              style={{ flex: 1, minHeight: '48px', maxHeight: '120px', resize: 'none', fontSize: '0.9rem' }}
              disabled={loading}
            />
            <button
              className="btn btn-primary"
              onClick={ask}
              disabled={loading || !question.trim()}
              style={{ alignSelf: 'flex-end', padding: '0.55rem 1rem' }}
            >
              {loading ? '…' : 'Ask →'}
            </button>
          </div>
        </div>
      </div>

      {/* Doc preview — opens on top of this panel (z-index 200/201) */}
      {panelDocId != null && (
        <DocPreviewPanel docId={panelDocId} onClose={() => setPanelDocId(null)} />
      )}
    </>
  )
}
