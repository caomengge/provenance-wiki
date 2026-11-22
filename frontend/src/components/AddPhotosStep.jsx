import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../api/client'
import { useIngest } from '../Ingest'

// Settings / Welcome → "Add your photos": put photographs in the photos
// folder, say where they come from (Source, required), then ingest. Shares its
// run, progress and Source with the sidebar's Ingest Photos button.

const muted = { color: 'var(--text-muted)', fontSize: '0.86rem', lineHeight: 1.5 }
const small = { fontSize: '0.82rem', padding: '0.3rem 0.7rem' }
const sub = { fontWeight: 600, color: 'var(--navy)', margin: '0.9rem 0 0.3rem', fontSize: '0.92rem' }
const box = (color, bg) => ({ border: `1px solid ${color}`, background: bg, borderRadius: '4px',
  padding: '0.5rem 0.75rem', fontSize: '0.84rem', margin: '0.5rem 0', lineHeight: 1.45 })
const AMBER = box('#d97706', '#fffbeb')
const GREEN = box('#16a34a', '#f0fdf4')
const BLUE = box('#93c5fd', '#eff6ff')

export default function AddPhotosStep({ archiveReady, anthropicReady, archiveStep, keysStep }) {
  const { ingesting, message, lastRun, source, setSource, start, setMessage } = useIngest()
  const [pending, setPending] = useState(null)
  const [suggestions, setSuggestions] = useState([])
  const [error, setError] = useState('')

  const refresh = () => {
    if (!archiveReady) return
    api.getPendingPhotos().then(setPending).catch(err => setError(err.message))
  }

  useEffect(() => {
    if (!archiveReady) return
    refresh()
    api.getArchives().then(r => setSuggestions(r.archives || [])).catch(() => {})
  }, [archiveReady]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (lastRun) refresh() }, [lastRun]) // eslint-disable-line react-hooks/exhaustive-deps

  const openFolder = () => api.openFolder('photos').then(() => setTimeout(refresh, 400)).catch(err => setError(err.message))

  if (!archiveReady) {
    return <p style={muted}>Create your archive in step {archiveStep} first.</p>
  }

  const blocked =
    !anthropicReady ? `Add your Anthropic API key in step ${keysStep} first: it is needed to read the photographs.`
    : !source.trim() ? 'Enter the Source above first.'
    : null

  return (
    <div>
      <p style={muted}>
        Provenance Wiki reads photographs of your documents (letters, receipts, auction records,
        labels…) and turns them into a searchable archive. Use photographs of test documents or copies
        while you’re trying it out.
      </p>

      <div style={sub}>a. Put your photographs in the photos folder</div>
      <p style={muted}>
        JPG, PNG, TIFF or WebP; one photograph per page. Multi-page documents can be grouped in the
        Gallery afterwards.
      </p>
      <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-ghost" style={small} onClick={openFolder}>Open photos folder</button>
        <button type="button" onClick={refresh}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem', color: 'var(--text-muted)', textDecoration: 'underline', fontFamily: 'inherit' }}>
          I’ve added photos: check again
        </button>
      </div>
      {pending && (
        <div style={pending.new_images ? BLUE : muted} role="status">
          {pending.new_images
            ? <>{pending.new_images} new photograph{pending.new_images === 1 ? '' : 's'} ready to ingest
                {pending.images > pending.new_images ? ` (${pending.images - pending.new_images} already in your archive)` : ''}.</>
            : pending.images
              ? <>All {pending.images} photographs in the folder are already in your archive.</>
              : <>The photos folder is empty.</>}
        </div>
      )}
      {pending?.unsupported?.length > 0 && (
        <div style={AMBER}>
          {pending.unsupported.map(u => `${u.count} ${u.ext.replace('.', '').toUpperCase()}`).join(', ')} file
          {pending.unsupported.reduce((n, u) => n + u.count, 0) === 1 ? '' : 's'} can’t be read and will be skipped.
          {pending.unsupported.some(u => u.ext === '.heic' || u.ext === '.heif') &&
            ' iPhone photos (HEIC): export or share them as JPG first.'}
          {pending.unsupported.some(u => u.ext === '.pdf') &&
            ' PDFs: save each page as an image first.'}
        </div>
      )}

      <div style={sub}>b. Say where these documents come from <span style={{ color: '#b45309' }}>(required)</span></div>
      <p style={muted}>
        The <strong>Source</strong> is the archive or collection the documents belong to, for example
        “Rijksmuseum Archive, dealer files” or “Private papers of A. Dealer”. It is saved with every
        document in this batch, so you can cite it and filter by it later.
        Ingest one source at a time: if your photographs come from several archives, add and ingest
        them in separate batches.
      </p>
      <input
        list="setup-source-suggestions"
        value={source}
        onChange={e => { setSource(e.target.value); setMessage('') }}
        placeholder="e.g. Rijksmuseum Archive, dealer files"
        aria-label="Source"
        style={{ width: '100%', maxWidth: '520px', boxSizing: 'border-box', fontSize: '0.9rem', padding: '0.4rem 0.6rem',
                 textTransform: 'none', letterSpacing: 'normal',
                 border: `1px solid ${source.trim() ? 'var(--border)' : '#d97706'}` }}
      />
      <datalist id="setup-source-suggestions">
        {suggestions.map(a => <option key={a} value={a} />)}
      </datalist>

      <div style={sub}>c. Ingest</div>
      <p style={muted}>
        Each photograph is sent to Anthropic to be read; your Anthropic account is charged per photograph.
        You can keep working while it runs. The <strong>Ingest Photos</strong> button in the sidebar does the same.
      </p>
      <button className="btn btn-primary" onClick={() => start(source)}
              disabled={ingesting || !!blocked}
              title={blocked || ''}
              style={{ fontSize: '0.9rem', padding: '0.45rem 1.1rem' }}>
        {ingesting ? '⟳ Ingesting…' : '⊕ Ingest Photos'}
      </button>
      {blocked && !ingesting && <div style={AMBER}>{blocked}</div>}
      {message && !(blocked && !ingesting && message.startsWith('Enter the Source')) && (
        <div style={lastRun ? GREEN : muted} role="status">
          {message}
          {lastRun && (
            <> · <Link to="/gallery">Open the Gallery</Link> · <Link to="/ingest-log">See the Ingest Log</Link></>
          )}
        </div>
      )}
      {error && <div style={AMBER}>{error}</div>}
    </div>
  )
}
