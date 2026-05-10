import React, { useState } from 'react'
import api from '../api/client'
import ScoreBadge from './TransactionScoreBadge'

const EMPTY = {
  seller: '', buyer: '', date: '', price: '', currency: '',
  auction_house: '', lot_number: '', location: '', notes: '',
}

const inputStyle = {
  width: '100%',
  fontFamily: 'inherit',
  fontSize: '0.85rem',
  background: 'var(--cream-bg)',
  border: '1px solid var(--gold)',
  borderRadius: '3px',
  padding: '0.2rem 0.4rem',
  outline: 'none',
  boxSizing: 'border-box',
  color: 'var(--text-body)',
}

function Field({ label, value, onChange, type = 'text' }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
      <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {label}
      </label>
      <input
        type={type}
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        style={inputStyle}
      />
    </div>
  )
}

function TransactionForm({ data, onChange, onSave, onCancel, onDelete, saving }) {
  return (
    <div style={{ borderLeft: '3px solid var(--gold)', paddingLeft: '0.75rem', marginBottom: '0.75rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem', marginBottom: '0.4rem' }}>
        <Field label="Seller"        value={data.seller}        onChange={v => onChange({ ...data, seller: v })} />
        <Field label="Buyer"         value={data.buyer}         onChange={v => onChange({ ...data, buyer: v })} />
        <Field label="Date (YYYY-MM-DD)" value={data.date}      onChange={v => onChange({ ...data, date: v })} />
        <Field label="Price"         value={data.price}         onChange={v => onChange({ ...data, price: v })} type="number" />
        <Field label="Currency"      value={data.currency}      onChange={v => onChange({ ...data, currency: v })} />
        <Field label="Auction House" value={data.auction_house} onChange={v => onChange({ ...data, auction_house: v })} />
        <Field label="Lot #"         value={data.lot_number}    onChange={v => onChange({ ...data, lot_number: v })} />
        <Field label="Location"      value={data.location}      onChange={v => onChange({ ...data, location: v })} />
      </div>
      <div style={{ marginBottom: '0.4rem' }}>
        <Field label="Notes" value={data.notes} onChange={v => onChange({ ...data, notes: v })} />
      </div>
      <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
        <button
          onClick={onSave}
          disabled={saving}
          style={{ fontSize: '0.8rem', padding: '0.2rem 0.6rem', background: 'var(--navy)', color: 'white', border: 'none', borderRadius: '3px', cursor: 'pointer' }}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          onClick={onCancel}
          disabled={saving}
          style={{ fontSize: '0.8rem', padding: '0.2rem 0.6rem', background: 'none', border: '1px solid var(--border)', borderRadius: '3px', cursor: 'pointer' }}
        >
          Cancel
        </button>
        {onDelete && (
          <button
            onClick={onDelete}
            disabled={saving}
            style={{ fontSize: '0.8rem', marginLeft: 'auto', padding: '0.2rem 0.6rem', background: 'none', border: '1px solid var(--border)', borderRadius: '3px', cursor: 'pointer', color: 'var(--text-muted)' }}
          >
            Delete
          </button>
        )}
      </div>
    </div>
  )
}

function TransactionRow({ t, onEdit }) {
  return (
    <div style={{ borderLeft: '3px solid var(--gold)', paddingLeft: '0.75rem', marginBottom: '0.75rem', display: 'flex', alignItems: 'flex-start', gap: '0.5rem' }}>
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.25rem' }}>
          {t.date && <span style={{ fontWeight: 600, color: 'var(--navy)' }}>{t.date}</span>}
          {typeof t.score === 'number' && <ScoreBadge score={t.score} style={{ marginLeft: '0.4rem' }} />}
        </div>
        {(t.seller || t.buyer) && (
          <div style={{ fontSize: '0.9rem' }}>
            {t.seller && <span><em>Seller:</em> {t.seller}</span>}
            {t.seller && t.buyer && <span style={{ margin: '0 0.5rem' }}>→</span>}
            {t.buyer && <span><em>Buyer:</em> {t.buyer}</span>}
          </div>
        )}
        {t.price && (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.88rem' }}>
            {t.currency} {Number(t.price).toLocaleString()}
            {t.auction_house && ` · ${t.auction_house}`}
            {t.lot_number && ` lot ${t.lot_number}`}
          </div>
        )}
        {t.location && <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>{t.location}</div>}
        {t.notes && <div style={{ fontSize: '0.85rem', fontStyle: 'italic', color: 'var(--text-muted)' }}>{t.notes}</div>}
      </div>
      <button
        onClick={() => onEdit(t)}
        style={{ fontSize: '0.75rem', padding: '0.15rem 0.5rem', background: 'none', border: '1px solid var(--border)', borderRadius: '3px', cursor: 'pointer', color: 'var(--text-muted)', flexShrink: 0 }}
      >
        Edit
      </button>
    </div>
  )
}

export default function TransactionEditor({ transactions: initial = [], docId, groupId }) {
  const [transactions, setTransactions] = useState(initial)
  const [editingId, setEditingId]       = useState(null)   // txn id or 'new'
  const [draft, setDraft]               = useState(null)
  const [saving, setSaving]             = useState(false)
  const [hideWeak, setHideWeak]         = useState(false)
  const isGroup = !!groupId

  const weakCount = transactions.filter(t => typeof t.score === 'number' && t.score < 2).length
  const visible = hideWeak ? transactions.filter(t => (t.score ?? 5) >= 2) : transactions

  const startEdit = (t) => { setEditingId(t.id); setDraft({ ...t }) }
  const startNew  = ()  => { setEditingId('new'); setDraft({ ...EMPTY }) }
  const cancel    = ()  => { setEditingId(null);  setDraft(null) }

  const handleSave = async () => {
    setSaving(true)
    try {
      if (editingId === 'new') {
        const created = isGroup
          ? await api.createGroupTransaction(groupId, draft)
          : await api.createTransaction(docId, draft)
        setTransactions(prev => [...prev, created])
      } else {
        const updated = isGroup
          ? await api.updateGroupTransaction(editingId, draft)
          : await api.updateTransaction(editingId, draft)
        setTransactions(prev => prev.map(t => t.id === editingId ? updated : t))
      }
      cancel()
    } catch (err) {
      alert('Save failed: ' + err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this transaction?')) return
    setSaving(true)
    try {
      isGroup
        ? await api.deleteGroupTransaction(id)
        : await api.deleteTransaction(id)
      setTransactions(prev => prev.filter(t => t.id !== id))
      cancel()
    } catch (err) {
      alert('Delete failed: ' + err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ marginBottom: '1.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem', gap: '0.5rem', flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', margin: 0 }}>
          Transactions ({hideWeak ? `${visible.length} of ${transactions.length}` : transactions.length})
        </h3>
        {weakCount > 0 && (
          <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: '0.3rem', cursor: 'pointer' }}>
            <input type="checkbox" checked={hideWeak} onChange={e => setHideWeak(e.target.checked)} />
            Hide weak (&lt; 2/5) · {weakCount}
          </label>
        )}
      </div>

      {visible.map(t =>
        editingId === t.id ? (
          <TransactionForm
            key={t.id}
            data={draft}
            onChange={setDraft}
            onSave={handleSave}
            onCancel={cancel}
            onDelete={() => handleDelete(t.id)}
            saving={saving}
          />
        ) : (
          <TransactionRow key={t.id} t={t} onEdit={startEdit} />
        )
      )}

      {editingId === 'new' ? (
        <TransactionForm
          data={draft}
          onChange={setDraft}
          onSave={handleSave}
          onCancel={cancel}
          onDelete={null}
          saving={saving}
        />
      ) : (
        <button
          onClick={startNew}
          style={{ fontSize: '0.8rem', padding: '0.25rem 0.75rem', background: 'none', border: '1px dashed var(--border)', borderRadius: '3px', cursor: 'pointer', color: 'var(--text-muted)', width: '100%' }}
        >
          + Add transaction
        </button>
      )}
    </div>
  )
}
