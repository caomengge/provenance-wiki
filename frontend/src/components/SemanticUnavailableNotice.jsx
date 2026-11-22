import React from 'react'
import { Link } from 'react-router-dom'

// Shown whenever the server reports that semantic retrieval was unavailable
// (no/invalid Voyage key, network failure, index not built). The server then
// degrades to keyword matching; the researcher must be told, never left to
// assume semantic search ran.
function reason(err) {
  if (!err) return null
  if (/VOYAGE_API_KEY is not set/.test(err)) return ' Reason: no Voyage AI key is saved.'
  if (/auth|401|invalid.*key/i.test(err)) return ' Reason: the Voyage AI key was rejected.'
  return ` Reason: ${err}.`
}

export default function SemanticUnavailableNotice({ retrieval, context = 'search' }) {
  if (!retrieval || retrieval.semantic_available !== false) return null
  const what = context === 'qa'
    ? 'This answer is based on keyword matches only, so relevant documents that use different wording may be missing.'
    : 'These are keyword results only, so documents that use different wording may be missing.'
  return (
    <div role="status" style={{
      border: '1px solid #d97706', background: '#fffbeb', color: '#92400e', borderRadius: '4px',
      padding: '0.55rem 0.85rem', fontSize: '0.84rem', margin: '0 0 0.9rem', lineHeight: 1.5,
    }}>
      <strong>Semantic search is unavailable.</strong> {what}
      {reason(retrieval.semantic_error)}{' '}
      Check your Voyage AI key in <Link to="/settings">Settings</Link>.
    </div>
  )
}
