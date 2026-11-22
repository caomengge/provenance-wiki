import React, { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import api from '../api/client'

// Settings and first-run setup: API keys (stored in the OS credential store,
// never in a file), what data leaves the computer, where the archive lives,
// version, and Quit. Opened automatically on first launch of the desktop app.

const PROVIDERS = [
  {
    id: 'anthropic',
    name: 'Anthropic API key',
    use: 'Reads your archival photographs (transcription and structured extraction) and writes answers to your questions.',
    link: 'https://console.anthropic.com/',
    linkLabel: 'Create a key in the Anthropic Console',
    testNote: 'Checking is free.',
  },
  {
    id: 'voyage',
    name: 'Voyage AI API key',
    use: 'Builds the search index that powers semantic search and finds the evidence used to answer questions.',
    link: 'https://www.voyageai.com/',
    linkLabel: 'Create a key at Voyage AI',
    testNote: 'Checking sends two words to Voyage AI: a tiny, billable request.',
  },
]

const card = {
  background: 'white', border: '1px solid var(--border)', borderRadius: '4px',
  padding: '1.1rem 1.25rem', marginBottom: '1.25rem',
}
const h2 = { fontFamily: 'var(--font-serif)', fontSize: '1.1rem', color: 'var(--navy)', margin: '0 0 0.6rem' }
const small = { fontSize: '0.82rem', padding: '0.3rem 0.7rem' }
const muted = { color: 'var(--text-muted)', fontSize: '0.86rem', lineHeight: 1.5 }

const STATUS_COLORS = { ok: '#15803d', invalid: '#b91c1c', quota: '#b45309', network: '#b45309', error: '#b91c1c', missing: '#6b7280' }

function KeyField({ provider, status, storeAvailable, storeName, onChanged }) {
  const [value, setValue] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)   // { status, text }

  const isSet = status?.set
  const fromEnv = status?.source === 'environment'

  const save = async () => {
    setBusy(true); setMsg(null)
    try {
      await api.saveApiKey(provider.id, value)
      setValue(''); setShow(false)
      setMsg({ status: 'ok', text: `Saved to ${storeName}.` })
      onChanged()
    } catch (err) {
      setMsg({ status: 'error', text: err.message })
    } finally { setBusy(false) }
  }

  const test = async () => {
    setBusy(true); setMsg(null)
    try {
      const res = await api.testApiKey(provider.id)
      setMsg({ status: res.status, text: res.message })
    } catch (err) {
      setMsg({ status: 'error', text: err.message })
    } finally { setBusy(false) }
  }

  const remove = async () => {
    setBusy(true); setMsg(null)
    try {
      await api.removeApiKey(provider.id)
      setMsg({ status: 'missing', text: 'Key removed from this computer.' })
      onChanged()
    } catch (err) {
      setMsg({ status: 'error', text: err.message })
    } finally { setBusy(false) }
  }

  return (
    <div style={{ padding: '0.9rem 0', borderTop: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.6rem', flexWrap: 'wrap' }}>
        <strong style={{ color: 'var(--navy)' }}>{provider.name}</strong>
        <span style={{ fontSize: '0.78rem', fontWeight: 600, color: isSet ? '#15803d' : '#b45309' }}>
          {isSet ? (fromEnv ? '● Set (from .env / environment)' : '● Saved') : '○ Not set'}
        </span>
      </div>
      <p style={{ ...muted, margin: '0.25rem 0 0.5rem' }}>
        {provider.use}{' '}
        <a href={provider.link} target="_blank" rel="noopener noreferrer">{provider.linkLabel}</a>.
      </p>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder={isSet ? 'Paste a new key to replace the saved one' : 'Paste your key'}
          autoComplete="off"
          spellCheck={false}
          aria-label={provider.name}
          style={{ flex: 1, minWidth: '260px', fontFamily: 'monospace', fontSize: '0.85rem' }}
        />
        <button className="btn btn-ghost" style={small} onClick={() => setShow(s => !s)} type="button">
          {show ? 'Hide' : 'Show'}
        </button>
        <button className="btn btn-primary" style={small} onClick={save}
                disabled={busy || !value.trim() || !storeAvailable}>
          Save
        </button>
        <button className="btn btn-ghost" style={small} onClick={test} disabled={busy || !isSet}
                title={provider.testNote}>
          Test connection
        </button>
        {isSet && !fromEnv && (
          <button className="btn btn-danger" style={small} onClick={remove} disabled={busy}>Remove</button>
        )}
      </div>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.3rem' }}>{provider.testNote}</div>
      {msg && (
        <div role="status" style={{ marginTop: '0.4rem', fontSize: '0.85rem', color: STATUS_COLORS[msg.status] || 'inherit' }}>
          {msg.text}
        </div>
      )}
    </div>
  )
}

export default function Settings({ onSettingsChanged }) {
  const [settings, setSettings] = useState(null)
  const [error, setError] = useState('')
  const [quitting, setQuitting] = useState(false)
  const [searchParams] = useSearchParams()
  const welcome = searchParams.get('welcome') === '1'

  const load = () => api.getSettings().then(setSettings).catch(err => setError(err.message))
  useEffect(() => { load() }, [])

  const changed = () => { load(); onSettingsChanged && onSettingsChanged() }

  const openFolder = (which) => api.openFolder(which).catch(err => setError(err.message))

  const quit = async () => {
    setQuitting(true)
    try { await api.quitApp() } catch (err) { setError(err.message); setQuitting(false) }
  }

  if (quitting) {
    return (
      <div style={{ padding: '3rem 2rem', maxWidth: '640px' }}>
        <h1 style={{ fontFamily: 'var(--font-serif)', color: 'var(--navy)' }}>Provenance Wiki has stopped</h1>
        <p style={muted}>You can close this browser tab. Your archive is saved on this computer. Open Provenance Wiki again from Applications whenever you need it.</p>
      </div>
    )
  }
  if (!settings) return <div className="loading" style={{ padding: '2rem' }}>{error || 'Loading…'}</div>

  const keys = settings.keys || {}
  const bothSet = keys.anthropic?.set && keys.voyage?.set

  return (
    <div style={{ padding: '2rem', maxWidth: '820px' }}>
      <h1 style={{ fontFamily: 'var(--font-serif)', fontSize: '1.5rem', color: 'var(--navy)', margin: '0 0 0.25rem' }}>
        {welcome ? 'Welcome to Provenance Wiki' : 'Settings'}
      </h1>
      <p style={{ ...muted, margin: '0 0 1.25rem' }}>
        Provenance Wiki turns photographs of archival documents (dealer letters, receipts, auction
        records, labels) into a searchable archive of people, places, objects, and transactions.
        Your archive is stored on this computer.
      </p>

      {error && <div style={{ ...card, borderColor: '#b91c1c', color: '#b91c1c' }}>{error}</div>}

      <section style={card}>
        <h2 style={h2}>1. Your API keys</h2>
        <p style={muted}>
          Provenance Wiki uses two AI services, paid for through <strong>your own accounts</strong>.
          These are <strong>API keys</strong> from each provider's developer console. A Claude.ai or
          ChatGPT subscription does <strong>not</strong> include API usage. Each provider bills you
          for what you use; Provenance Wiki includes no prepaid credit.
        </p>
        {settings.secure_store_available ? (
          <p style={muted}>Keys are stored in your {settings.secure_store_name}, not in a file, and are never shown again after saving.</p>
        ) : (
          <p style={{ ...muted, color: '#b91c1c' }}>
            Secure key storage ({settings.secure_store_name}) is not available on this computer, so keys can't be saved here.
            {settings.packaged ? '' : ' Developers can use the .env file instead.'}
          </p>
        )}
        {PROVIDERS.map(p => (
          <KeyField key={p.id} provider={p} status={keys[p.id]}
                    storeAvailable={settings.secure_store_available}
                    storeName={settings.secure_store_name} onChanged={changed} />
        ))}
        {!bothSet && (
          <p style={{ ...muted, marginTop: '0.75rem' }}>
            You can skip this for now and browse your archive or use keyword search. Without an
            Anthropic key, new photographs can't be read and questions can't be answered. Without a
            Voyage AI key, semantic search is unavailable and answers can only draw on keyword matches.
          </p>
        )}
      </section>

      <section style={card}>
        <h2 style={h2}>2. What leaves this computer</h2>
        <ul style={{ ...muted, margin: '0 0 0.5rem', paddingLeft: '1.2rem' }}>
          <li><strong>Photographs you ingest</strong> are sent to Anthropic to be read and transcribed.</li>
          <li><strong>Document text</strong> (transcriptions and generated descriptions) is sent to Voyage AI to build the search index.</li>
          <li><strong>When you ask a question</strong>, your question and the selected evidence passages are sent to Anthropic.</li>
          <li>Nothing else is sent anywhere. There is no Provenance Wiki account, server, or analytics.</li>
        </ul>
        <p style={muted}>
          Before processing restricted or confidential material, confirm that your institution's
          permissions and confidentiality requirements allow sending it to these services.
        </p>
      </section>

      <section style={card}>
        <h2 style={h2}>3. Where your archive is stored</h2>
        <p style={muted}>
          To add documents, put photographs in your <strong>Photos folder</strong>, then click
          <strong> Ingest Photos</strong> in the sidebar.
        </p>
        <table style={{ fontSize: '0.84rem', borderCollapse: 'collapse', width: '100%' }}>
          <tbody>
            {[['photos', 'Photos folder'], ['data', 'Archive folder (database, thumbnails, backups)'], ['logs', 'Logs']].map(([k, label]) => (
              <tr key={k}>
                <td style={{ padding: '0.3rem 0.6rem 0.3rem 0', whiteSpace: 'nowrap', verticalAlign: 'top' }}>{label}</td>
                <td style={{ padding: '0.3rem 0.6rem', fontFamily: 'monospace', fontSize: '0.78rem', wordBreak: 'break-all' }}>{settings.folders[k]}</td>
                <td style={{ padding: '0.3rem 0', textAlign: 'right' }}>
                  <button className="btn btn-ghost" style={small} onClick={() => openFolder(k)}>Open</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p style={{ ...muted, marginTop: '0.5rem' }}>
          {settings.packaged
            ? 'Uninstalling the app does not delete this folder. To back up your archive, copy the whole archive folder (it contains your photographs too) while Provenance Wiki is not running.'
            : 'To back up your archive, copy both the archive folder and the photos folder while Provenance Wiki is not running.'}
        </p>
      </section>

      <section style={card}>
        <h2 style={h2}>About</h2>
        <p style={muted}>
          Version {settings.version}
          {settings.build?.commit ? ` · build ${settings.build.commit}` : ''}
          {settings.build?.platform ? ` · ${settings.build.platform}` : ''}
        </p>
        {settings.testing_build && (
          <p style={{ ...muted, color: '#b45309' }}>
            This is a <strong>testing build</strong>. It is not signed or notarized for public
            distribution. Use it for evaluation, with test material or copies of your documents.
          </p>
        )}
        {settings.packaged && (
          <button className="btn btn-ghost" style={small} onClick={quit}>Quit Provenance Wiki</button>
        )}
      </section>
    </div>
  )
}
