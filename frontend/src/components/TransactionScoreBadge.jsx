import React from 'react'

/**
 * Coloured 0/5–5/5 badge for transaction quality. Score counts how many of
 * {seller, buyer, date, price, auction_house} are filled.
 *
 * Used in TransactionEditor (document/group detail rows) and Timeline events.
 */
export function scoreColor(score) {
  if (score >= 3)  return { bg: '#dcf5e3', border: '#3a9d5a', text: '#1f6b3a' }   // green
  if (score === 2) return { bg: '#fcf3d4', border: '#b88a1f', text: '#7a5a0e' }   // amber
  return { bg: '#fbe1e1', border: '#c14a4a', text: '#871f1f' }                    // red 0–1
}

export default function TransactionScoreBadge({ score, style }) {
  const c = scoreColor(score)
  return (
    <span
      title={`Quality score: ${score} of 5 anchor fields (seller, buyer, date, price, auction_house) present`}
      style={{
        display: 'inline-block',
        fontSize: '0.7rem',
        fontWeight: 700,
        padding: '0.1rem 0.4rem',
        borderRadius: '3px',
        background: c.bg,
        border: `1px solid ${c.border}`,
        color: c.text,
        whiteSpace: 'nowrap',
        ...style,
      }}
    >
      {score}/5
    </span>
  )
}
