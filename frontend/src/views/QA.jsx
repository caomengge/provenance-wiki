import React, { useState, useRef, useEffect } from 'react'
import api from '../api/client'
import DocPreviewPanel from '../components/DocPreviewPanel'

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

export default function QA() {
  const [history, setHistory]     = useState([])
  const [question, setQuestion]   = useState('')
  const [loading, setLoading]     = useState(false)
  const [panelDocId, setPanelDocId] = useState(null)
  const bottomRef = useRef(null)
  const inputRef  = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history])

  const ask = async () => {
    const q = question.trim()
    if (!q || loading) return

    setQuestion('')
    setLoading(true)

    // Add user message
    const userEntry = { role: 'user', text: q }
    setHistory(h => [...h, userEntry])

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
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      ask()
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <div className="page-header" style={{ flexShrink: 0 }}>
        <h1>Research Assistant</h1>
        <div className="subtitle">Ask natural-language questions about provenance — answers are grounded in your archive</div>
      </div>

      {/* Chat area */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '1.5rem 2.5rem' }}>
        {history.length === 0 ? (
          <div style={{ maxWidth: '600px', margin: '0 auto' }}>
            <div style={{
              background: 'var(--cream-card)',
              border: '1px solid var(--border)',
              borderRadius: '4px',
              padding: '1.5rem',
              marginBottom: '1.5rem',
            }}>
              <h3 style={{ marginBottom: '0.5rem', color: 'var(--navy)' }}>How to use</h3>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.92rem' }}>
                Ask any provenance research question. The system will search your archive,
                retrieve the most relevant documents, and give you a grounded answer
                citing specific source documents.
              </p>
            </div>
            <div>
              <div style={{ fontSize: '0.85rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                Example questions
              </div>
              {EXAMPLE_QUESTIONS.map((q, i) => (
                <button
                  key={i}
                  onClick={() => { setQuestion(q); inputRef.current?.focus() }}
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    background: 'var(--cream-card)',
                    border: '1px solid var(--border)',
                    borderRadius: '3px',
                    padding: '0.6rem 0.9rem',
                    marginBottom: '0.4rem',
                    cursor: 'pointer',
                    fontFamily: 'var(--font-serif)',
                    fontSize: '0.92rem',
                    color: 'var(--navy)',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--cream-bg)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'var(--cream-card)'}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ maxWidth: '760px', margin: '0 auto' }}>
            {history.map((entry, i) => (
              <div key={i} style={{ marginBottom: '1.5rem' }}>
                {entry.role === 'user' ? (
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <div style={{
                      background: 'var(--navy)',
                      color: 'var(--cream-light)',
                      borderRadius: '12px 12px 3px 12px',
                      padding: '0.75rem 1.1rem',
                      maxWidth: '75%',
                      fontFamily: 'var(--font-serif)',
                      fontSize: '0.95rem',
                      lineHeight: 1.6,
                    }}>
                      {entry.text}
                    </div>
                  </div>
                ) : (
                  <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                    <div style={{
                      width: '32px',
                      height: '32px',
                      borderRadius: '50%',
                      background: 'var(--gold)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '0.85rem',
                      fontWeight: 700,
                      color: 'var(--navy)',
                      flexShrink: 0,
                      marginTop: '2px',
                    }}>
                      A
                    </div>
                    <div style={{ flex: 1 }}>
                      {/* Confidence badge */}
                      {entry.confidence && (
                        <div style={{ marginBottom: '0.5rem' }}>
                          <span style={{
                            ...CONFIDENCE_COLORS[entry.confidence],
                            display: 'inline-block',
                            padding: '1px 8px',
                            borderRadius: '10px',
                            fontSize: '0.75rem',
                            fontWeight: 600,
                            letterSpacing: '0.03em',
                          }}>
                            {CONFIDENCE_COLORS[entry.confidence].label}
                          </span>
                        </div>
                      )}

                      {/* Answer text */}
                      <div style={{
                        background: 'var(--cream-card)',
                        border: '1px solid var(--border)',
                        borderRadius: '3px 12px 12px 12px',
                        padding: '1rem 1.25rem',
                        lineHeight: 1.75,
                        fontSize: '0.95rem',
                        whiteSpace: 'pre-wrap',
                      }}>
                        {entry.answer}
                      </div>

                      {/* Citations */}
                      {entry.citations?.length > 0 && (
                        <div style={{ marginTop: '0.6rem' }}>
                          <div style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', marginBottom: '0.35rem' }}>
                            Sources
                          </div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                            {entry.citations.map(c => (
                              <button
                                key={c.doc_id}
                                onClick={() => setPanelDocId(c.doc_id)}
                                style={{
                                  display: 'inline-block',
                                  background: 'var(--cream-bg)',
                                  border: '1px solid var(--border)',
                                  borderRadius: '3px',
                                  padding: '0.2rem 0.6rem',
                                  fontSize: '0.8rem',
                                  color: 'var(--navy)',
                                  cursor: 'pointer',
                                  fontFamily: 'inherit',
                                }}
                              >
                                Doc #{c.doc_id}: {c.title?.substring(0, 30)}{c.title?.length > 30 ? '…' : ''}
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
              <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', color: 'var(--text-muted)', fontSize: '0.9rem', fontStyle: 'italic' }}>
                <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: 'var(--gold)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.85rem', fontWeight: 700, color: 'var(--navy)', flexShrink: 0 }}>A</div>
                Searching archive and composing answer…
              </div>
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div style={{
        borderTop: '1px solid var(--border)',
        background: 'var(--cream-light)',
        padding: '1rem 2.5rem',
        flexShrink: 0,
      }}>
        <div style={{ maxWidth: '760px', margin: '0 auto', display: 'flex', gap: '0.75rem' }}>
          <textarea
            ref={inputRef}
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a provenance research question… (Enter to send, Shift+Enter for new line)"
            style={{ flex: 1, minHeight: '56px', maxHeight: '140px', resize: 'none', fontSize: '0.95rem' }}
            disabled={loading}
          />
          <button
            className="btn btn-primary"
            onClick={ask}
            disabled={loading || !question.trim()}
            style={{ alignSelf: 'flex-end', padding: '0.6rem 1.25rem' }}
          >
            {loading ? '…' : 'Ask →'}
          </button>
        </div>
        {history.length > 0 && (
          <div style={{ maxWidth: '760px', margin: '0.5rem auto 0', textAlign: 'right' }}>
            <button
              className="btn btn-ghost"
              onClick={() => setHistory([])}
              style={{ fontSize: '0.8rem', padding: '0.2rem 0.6rem' }}
            >
              Clear conversation
            </button>
          </div>
        )}
      </div>

      {panelDocId != null && (
        <DocPreviewPanel docId={panelDocId} onClose={() => setPanelDocId(null)} />
      )}
    </div>
  )
}
