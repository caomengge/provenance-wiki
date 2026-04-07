import React from 'react'
import { useNavigate } from 'react-router-dom'

/** Clickable badge displaying an entity (person, object, institution). */
export default function EntityTag({ entity, onClick, size = 'sm' }) {
  const navigate = useNavigate()

  const typeClass = {
    person: 'badge-person',
    object: 'badge-object',
    institution: 'badge-institution',
  }[entity?.type] || 'badge-unknown'

  const handleClick = (e) => {
    e.stopPropagation()
    if (onClick) {
      onClick(entity)
    } else if (entity?.id) {
      navigate(`/search?entity_id=${entity.id}`)
    }
  }

  const fontSize = size === 'lg' ? '0.9rem' : '0.78rem'

  return (
    <span
      className={`badge ${typeClass}`}
      style={{
        cursor: 'pointer',
        fontSize,
        marginRight: '0.3rem',
        marginBottom: '0.3rem',
        display: 'inline-block',
      }}
      onClick={handleClick}
      title={`${entity?.type}: ${entity?.name}${entity?.role ? ` (${entity.role})` : ''}`}
    >
      {entity?.name}
    </span>
  )
}
