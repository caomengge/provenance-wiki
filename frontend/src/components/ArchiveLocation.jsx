import React, { useEffect, useRef, useState } from 'react'
import api from '../api/client'

// Archive location screens (spec: specs/archive-location.md):
//   ArchiveSetupStep      first launch: where to store the archive
//   ArchiveSettings       Settings: location, size, free space, Change location…
//   ArchiveMissingScreen  the archive's folder/drive can't be found
//   ArchiveMovingScreen   shown elsewhere in the app while a move runs

export function fmtBytes(n) {
  if (n == null) return '—'
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

const small = { fontSize: '0.82rem', padding: '0.3rem 0.7rem' }
const muted = { color: 'var(--text-muted)', fontSize: '0.86rem', lineHeight: 1.5 }
const mono = { fontFamily: 'monospace', fontSize: '0.8rem', wordBreak: 'break-all', textTransform: 'none', letterSpacing: 'normal' }
const plainLabel = { display: 'block', margin: '0.6rem 0', cursor: 'pointer', textTransform: 'none', letterSpacing: 'normal', fontSize: '0.9rem', color: 'inherit', fontWeight: 'normal' }
const note = (color, bg) => ({
  border: `1px solid ${color}`, background: bg, borderRadius: '4px',
  padding: '0.5rem 0.75rem', fontSize: '0.84rem', margin: '0.5rem 0', lineHeight: 1.45,
})
const RED = note('#dc2626', '#fef2f2')
const AMBER = note('#d97706', '#fffbeb')
const GREEN = note('#16a34a', '#f0fdf4')

function PickerWaiting() {
  const isWin = navigator.platform.startsWith('Win')
  return (
    <div style={AMBER} role="status">
      A folder window has opened. If you don’t see it, it may be behind this browser
      window{isWin ? ': look for it on the taskbar at the bottom of the screen' : ''}.{' '}
      <button type="button" onClick={() => api.cancelChooseArchiveFolder().catch(() => {})}
              style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', textDecoration: 'underline', fontFamily: 'inherit', fontSize: 'inherit', color: 'inherit' }}>
        Cancel
      </button>
    </div>
  )
}

// ── Folder chooser with live checking ────────────────────────────────────────

function FolderChooser({ purpose, onResult, autoPick = false }) {
  const [typed, setTyped] = useState('')
  const [typing, setTyping] = useState(false)
  const [busy, setBusy] = useState(false)
  const [picking, setPicking] = useState(false)
  const [check, setCheck] = useState(null)
  const [msg, setMsg] = useState('')
  const isWin = navigator.platform.startsWith('Win')

  const cancelPick = () => { api.cancelChooseArchiveFolder().catch(() => {}); setPicking(false) }

  const runCheck = async (path) => {
    if (!path) return
    setBusy(true); setMsg('')
    try {
      const res = await api.checkArchiveDestination(path, purpose)
      setCheck(res); onResult(res)
    } catch (err) {
      setMsg(err.message); onResult(null)
    } finally { setBusy(false) }
  }

  const pick = async () => {
    setPicking(true); setMsg('')
    try {
      const res = await api.chooseArchiveFolder()
      if (!res.cancelled && res.path) { setTyped(res.path); await runCheck(res.path) }
    } catch (err) {
      setTyping(true)
      setMsg(err.status === 501 ? 'The folder picker isn\u2019t available here. Type or paste the folder path below.' : err.message)
    } finally { setPicking(false) }
  }

  useEffect(() => { if (autoPick) pick() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        {!picking ? (
          <button className="btn btn-ghost" style={small} onClick={pick} disabled={busy}>Choose folder…</button>
        ) : (
          <button className="btn btn-ghost" style={small} onClick={cancelPick}>Cancel folder window</button>
        )}
        {!typing && (
          <button type="button" onClick={() => setTyping(true)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem',
                           color: 'var(--text-muted)', textDecoration: 'underline', fontFamily: 'inherit' }}>
            Type a folder path instead
          </button>
        )}
      </div>
      {picking && (
        <div style={AMBER} role="status">
          A folder window has opened. If you don’t see it, it may be behind this browser
          window{isWin ? ': look for it on the taskbar at the bottom of the screen' : ''}.
          Or type the folder path instead.
        </div>
      )}
      {typing && (
        <form onSubmit={e => { e.preventDefault(); runCheck(typed) }}
              style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
          <input value={typed} onChange={e => setTyped(e.target.value)}
                 placeholder={navigator.platform.startsWith('Win') ? 'D:\\Research' : '/Volumes/Archive'}
                 aria-label="Folder path" spellCheck={false}
                 style={{ flex: 1, ...mono, fontSize: '0.85rem' }} />
          <button className="btn btn-ghost" style={small} type="submit" disabled={busy || !typed.trim()}>Check</button>
        </form>
      )}
      {msg && <div style={AMBER}>{msg}</div>}
      {check && (
        <div style={{ marginTop: '0.5rem' }}>
          {check.final_path && (
            <div style={muted}>Archive folder: <span style={mono}>{check.final_path}</span>
              {check.free_bytes ? ` · ${fmtBytes(check.free_bytes)} free on ${check.drive}` : ''}</div>
          )}
          {check.refusal && <div style={RED} role="alert">{check.refusal}</div>}
          {(check.warnings || []).map((w, i) => <div key={i} style={AMBER}>{w}</div>)}
        </div>
      )}
    </div>
  )
}

// ── First launch ─────────────────────────────────────────────────────────────

export function ArchiveSetupStep({ location: given, onDone, confirmNew = false }) {
  const [location, setLocation] = useState(given)
  useEffect(() => { if (!given) api.getArchiveLocation(false).then(setLocation).catch(() => {}) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const [mode, setMode] = useState('default')     // default | other | existing
  const [check, setCheck] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [existing, setExisting] = useState('')
  const [picking, setPicking] = useState(false)

  const create = async () => {
    setBusy(true); setError('')
    try {
      const body = mode === 'default' ? { use_default: true } : { path: check.final_path }
      if (confirmNew) body.confirm_new = true
      await api.setupArchive(body)
      onDone()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const openExisting = async () => {
    setPicking(true); setError('')
    try {
      const res = await api.chooseArchiveFolder()
      const path = res.cancelled ? null : res.path
      if (path) { await api.locateArchive(path, true); onDone() }
    } catch (err) {
      if (err.status === 501) setMode('existing-typed')
      else setError(err.message)
    } finally { setPicking(false) }
  }

  const openTyped = async () => {
    setBusy(true); setError('')
    try { await api.locateArchive(existing, true); onDone() }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const defaultFree = location?.default_free_bytes
  const lowDefault = defaultFree != null && defaultFree < 20 * 1024 ** 3
  const ready = mode === 'default' || (mode === 'other' && check?.ok)

  return (
    <div>
      <p style={muted}>
        Your archive holds your photographs and grows as you add them (roughly 5–15 GB per
        1,000 photos). Everything is kept together in one folder. If this computer's main drive
        is small, choose a folder on a larger drive.
      </p>
      <label style={plainLabel}>
        <input type="radio" checked={mode === 'default'} onChange={() => setMode('default')} />{' '}
        <strong>Use the suggested location</strong>
        <div style={{ ...muted, marginLeft: '1.4rem' }}>
          <span style={mono}>{location?.default_path}</span>
          {defaultFree != null && <> · {fmtBytes(defaultFree)} free on {location.default_drive}</>}
        </div>
        {lowDefault && mode === 'default' && (
          <div style={{ ...AMBER, marginLeft: '1.4rem' }}>
            This drive has limited free space. Consider choosing a folder on a larger drive.
          </div>
        )}
      </label>
      <label style={plainLabel}>
        <input type="radio" checked={mode === 'other'} onChange={() => setMode('other')} />{' '}
        <strong>Choose another folder</strong>
      </label>
      {mode === 'other' && (
        <div style={{ marginLeft: '1.4rem' }}>
          <FolderChooser purpose="setup" onResult={setCheck} autoPick />
        </div>
      )}
      {mode === 'existing-typed' && (
        <form onSubmit={e => { e.preventDefault(); openTyped() }} style={{ display: 'flex', gap: '0.5rem' }}>
          <input value={existing} onChange={e => setExisting(e.target.value)} aria-label="Existing archive folder"
                 placeholder="Folder containing your Provenance Wiki Archive" style={{ flex: 1, ...mono }} />
          <button className="btn btn-primary" style={small} disabled={busy || !existing.trim()}>Open</button>
        </form>
      )}
      {error && <div style={RED} role="alert">{error}</div>}
      {picking && <PickerWaiting />}
      <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: '0.75rem', flexWrap: 'wrap' }}>
        {mode !== 'existing-typed' && (
          <button className="btn btn-primary" style={small} onClick={create} disabled={busy || !ready}>
            {busy ? 'Creating…' : 'Create my archive here'}
          </button>
        )}
        {!confirmNew && <button type="button" onClick={openExisting} disabled={busy || picking}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.82rem',
                         color: 'var(--text-muted)', textDecoration: 'underline', fontFamily: 'inherit' }}>
          I already have a Provenance Wiki archive (e.g. from another computer)…
        </button>}
      </div>
    </div>
  )
}

// ── Settings: location, Change location…, old copy ───────────────────────────

const PHASE_LABEL = {
  preparing: 'Preparing…', copying: 'Copying your archive…', checking: 'Checking the copy…',
  switching: 'Switching to the new location…',
}

export function ArchiveSettings({ onChanged }) {
  const [loc, setLoc] = useState(null)
  const [error, setError] = useState('')
  const [stage, setStage] = useState('idle')   // idle | choose | confirm | moving | finished
  const [check, setCheck] = useState(null)
  const [status, setStatus] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [busy, setBusy] = useState(false)
  const [switchTo, setSwitchTo] = useState(null)   // { path } awaiting confirmation
  const timer = useRef(null)

  const load = () => api.getArchiveLocation(true).then(l => {
    setLoc(l)
    if (l.state === 'moving' && l.move) { setStage('moving'); setStatus(l.move); poll() }
  }).catch(err => setError(err.message))

  useEffect(() => { load(); return () => clearTimeout(timer.current) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const poll = () => {
    clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      try {
        const s = await api.getMoveStatus()
        setStatus(s)
        if (['done', 'failed', 'cancelled'].includes(s.phase)) {
          setStage('finished'); load(); onChanged && onChanged()
        } else poll()
      } catch { poll() }
    }, 500)
  }

  const start = async () => {
    setBusy(true); setError('')
    try {
      const s = await api.moveArchive(check.final_path)
      setStatus(s); setStage('moving'); poll()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const deleteOld = async () => {
    setBusy(true); setError('')
    try { setLoc({ ...(await api.deleteOldArchiveCopy()), size_bytes: loc.size_bytes }); setConfirmDelete(false) }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const [picking, setPicking] = useState(false)
  const pickOther = async () => {
    setError(''); setPicking(true)
    try {
      const res = await api.chooseArchiveFolder()
      if (!res.cancelled && res.path) setSwitchTo({ path: res.path })
    } catch (err) { setError(err.status === 501 ? 'The folder picker isn’t available here.' : err.message) }
    finally { setPicking(false) }
  }

  const openOther = async () => {
    setBusy(true); setError('')
    try { await api.locateArchive(switchTo.path, true); setSwitchTo(null); load(); onChanged && onChanged() }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const keepOld = async () => {
    try { setLoc({ ...(await api.forgetOldArchiveCopy()), size_bytes: loc.size_bytes }) } catch (err) { setError(err.message) }
  }

  if (!loc) return <p style={muted}>{error || 'Loading…'}</p>
  if (!loc.packaged) {
    return <p style={muted}>Archive folder: <span style={mono}>{loc.path}</span> (developer setup: change it with <code>PW_DATA_DIR</code>).</p>
  }

  const pct = status && status.bytes_total ? Math.min(100, Math.round(100 * status.bytes_done / status.bytes_total)) : 0
  const lowSpace = loc.free_bytes != null && loc.free_bytes < 2 * 1024 ** 3

  return (
    <div>
      <table style={{ fontSize: '0.84rem', borderCollapse: 'collapse', width: '100%' }}>
        <tbody>
          <tr>
            <td style={{ padding: '0.3rem 0.6rem 0.3rem 0', whiteSpace: 'nowrap', verticalAlign: 'top' }}>Location</td>
            <td style={{ padding: '0.3rem 0.6rem', ...mono }}>{loc.path}</td>
            <td style={{ textAlign: 'right' }}>
              <button className="btn btn-ghost" style={small} onClick={() => api.openFolder('data').catch(e => setError(e.message))}>Open</button>
            </td>
          </tr>
          <tr>
            <td style={{ padding: '0.3rem 0.6rem 0.3rem 0' }}>Size</td>
            <td style={{ padding: '0.3rem 0.6rem' }} colSpan={2}>{fmtBytes(loc.size_bytes)} (photographs, database, thumbnails, backups and logs)</td>
          </tr>
          <tr>
            <td style={{ padding: '0.3rem 0.6rem 0.3rem 0' }}>Free space</td>
            <td style={{ padding: '0.3rem 0.6rem' }} colSpan={2}>{fmtBytes(loc.free_bytes)} free on {loc.drive}</td>
          </tr>
        </tbody>
      </table>
      {lowSpace && <div style={AMBER}>The drive holding your archive is almost full. Consider moving the archive to a larger drive.</div>}
      {loc.removable && <div style={AMBER}>Your archive is on a removable or external drive. Keep it connected while you work, and quit Provenance Wiki before unplugging it.</div>}

      {loc.previous_copy && stage !== 'moving' && (
        <div style={AMBER}>
          The old copy of your archive is still at <span style={mono}>{loc.previous_copy.path}</span> ({fmtBytes(loc.previous_copy.size_bytes)}).
          {!confirmDelete ? (
            <div style={{ marginTop: '0.4rem', display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-danger" style={small} onClick={() => setConfirmDelete(true)}>Delete the old copy…</button>
              <button className="btn btn-ghost" style={small} onClick={keepOld}>Keep it, stop reminding me</button>
            </div>
          ) : (
            <div style={{ marginTop: '0.4rem' }}>
              <strong>Permanently delete the old copy?</strong> Your archive at the new location is not affected. This cannot be undone.
              <div style={{ marginTop: '0.4rem', display: 'flex', gap: '0.5rem' }}>
                <button className="btn btn-danger" style={small} onClick={deleteOld} disabled={busy}>{busy ? 'Deleting…' : 'Delete permanently'}</button>
                <button className="btn btn-ghost" style={small} onClick={() => setConfirmDelete(false)}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}

      {error && <div style={RED} role="alert">{error}</div>}

      {stage === 'idle' && !switchTo && (
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center', marginTop: '0.5rem', flexWrap: 'wrap' }}>
          <button className="btn btn-ghost" style={small} onClick={() => { setStage('choose'); setCheck(null) }}>
            Change location…
          </button>
          <button type="button" onClick={pickOther}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem', color: 'var(--text-muted)', textDecoration: 'underline', fontFamily: 'inherit' }}>
            Open a different archive…
          </button>
        </div>
      )}
      {picking && <PickerWaiting />}
      {switchTo && (
        <div style={AMBER}>
          Switch to the archive in <span style={mono}>{switchTo.path}</span>? Your current archive stays where it is and
          can be opened again the same way.
          <div style={{ marginTop: '0.4rem', display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-ghost" style={small} onClick={openOther} disabled={busy}>Switch</button>
            <button className="btn btn-ghost" style={small} onClick={() => setSwitchTo(null)}>Cancel</button>
          </div>
        </div>
      )}

      {(stage === 'choose' || stage === 'confirm') && (
        <div style={{ border: '1px solid var(--border)', borderRadius: '4px', padding: '0.75rem 0.9rem', marginTop: '0.6rem' }}>
          <strong>Move your archive</strong>
          <p style={muted}>Choose a folder on a drive with enough free space. A folder named “Provenance Wiki Archive” is created inside it.</p>
          <FolderChooser purpose="move" onResult={r => { setCheck(r); setStage('choose') }} autoPick />
          {check?.ok && (
            <div style={GREEN}>
              Provenance Wiki will copy your archive ({fmtBytes(loc.size_bytes)}) to <span style={mono}>{check.final_path}</span>.
              Searching and editing pause while it copies. Your current archive is not changed until the copy has been checked.
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
            <button className="btn btn-primary" style={small} onClick={start} disabled={busy || !check?.ok}>Copy and switch</button>
            <button className="btn btn-ghost" style={small} onClick={() => { setStage('idle'); setError('') }}>Cancel</button>
          </div>
        </div>
      )}

      {stage === 'moving' && status && (
        <div style={{ border: '1px solid var(--border)', borderRadius: '4px', padding: '0.75rem 0.9rem', marginTop: '0.6rem' }} role="status">
          <strong>{PHASE_LABEL[status.phase] || 'Moving…'}</strong>
          <div style={{ height: '8px', background: 'var(--border)', borderRadius: '4px', margin: '0.5rem 0', overflow: 'hidden' }}>
            <div style={{ width: `${status.phase === 'checking' || status.phase === 'switching' ? 100 : pct}%`, height: '100%', background: 'var(--gold)', transition: 'width 0.3s' }} />
          </div>
          <div style={muted}>
            {status.files_done} of {status.files_total} files · {fmtBytes(status.bytes_done)} of {fmtBytes(status.bytes_total)}
            {status.current_file ? <> · <span style={mono}>{status.current_file}</span></> : null}
          </div>
          {status.phase !== 'switching' && (
            <button className="btn btn-ghost" style={{ ...small, marginTop: '0.4rem' }} onClick={() => api.cancelMove()}>Cancel</button>
          )}
        </div>
      )}

      {stage === 'finished' && status && (
        <div style={status.phase === 'done' ? GREEN : RED} role="status">
          {status.phase === 'done'
            ? <>Your archive now lives at <span style={mono}>{status.to}</span>.</>
            : status.error}
          <div><button className="btn btn-ghost" style={{ ...small, marginTop: '0.4rem' }} onClick={() => setStage('idle')}>OK</button></div>
        </div>
      )}
    </div>
  )
}

// ── Archive can't be found ───────────────────────────────────────────────────

export function ArchiveMissingScreen({ expectedPath, onResolved }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [different, setDifferent] = useState(null)   // path awaiting "open anyway"
  const [startNew, setStartNew] = useState(false)
  const [typed, setTyped] = useState('')
  const [typing, setTyping] = useState(false)

  const retry = async () => {
    setBusy(true); setMsg('')
    try {
      const res = await api.retryArchive()
      if (res.state === 'ok') onResolved()
      else setMsg('Still not found. Check that the drive is connected, or locate the archive.')
    } catch (err) { setMsg(err.message) } finally { setBusy(false) }
  }

  const locate = async (path, allowDifferent = false) => {
    setBusy(true); setMsg('')
    try { await api.locateArchive(path, allowDifferent); onResolved() }
    catch (err) {
      if (err.data?.code === 'different_archive') setDifferent(path)
      else setMsg(err.message)
    } finally { setBusy(false) }
  }

  const [picking, setPicking] = useState(false)
  const pickAndLocate = async () => {
    setPicking(true); setMsg('')
    try {
      const res = await api.chooseArchiveFolder()
      setPicking(false)
      if (!res.cancelled && res.path) await locate(res.path)
    } catch (err) {
      setPicking(false)
      if (err.status === 501) setTyping(true); else setMsg(err.message)
    }
  }

  return (
    <div style={{ padding: '3rem 2rem', maxWidth: '720px' }}>
      <h1 style={{ fontFamily: 'var(--font-serif)', fontSize: '1.5rem', color: 'var(--navy)' }}>Your archive can’t be found</h1>
      <p style={muted}>Provenance Wiki expected your archive here:</p>
      <p style={{ ...mono, fontSize: '0.9rem' }}>{expectedPath}</p>
      <p style={muted}>If it’s on an external drive, connect the drive and click <strong>Try again</strong>.
        If the drive now has a different letter or name, or you moved the folder yourself, use <strong>Locate my archive</strong>.</p>

      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', margin: '1rem 0' }}>
        <button className="btn btn-primary" onClick={retry} disabled={busy}>Try again</button>
        <button className="btn btn-ghost" onClick={pickAndLocate} disabled={busy || picking}>Locate my archive…</button>
        <button type="button" onClick={() => setTyping(true)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.8rem', color: 'var(--text-muted)', textDecoration: 'underline', fontFamily: 'inherit' }}>
          Type the folder path instead
        </button>
      </div>
      {picking && <PickerWaiting />}
      {typing && (
        <form onSubmit={e => { e.preventDefault(); locate(typed) }} style={{ display: 'flex', gap: '0.5rem' }}>
          <input value={typed} onChange={e => setTyped(e.target.value)} aria-label="Archive folder"
                 placeholder="Folder containing your Provenance Wiki Archive" style={{ flex: 1, ...mono }} />
          <button className="btn btn-ghost" disabled={busy || !typed.trim()}>Open</button>
        </form>
      )}
      {msg && <div style={AMBER}>{msg}</div>}
      {different && (
        <div style={AMBER}>
          That folder contains a <strong>different</strong> Provenance Wiki archive from the one this computer was using.
          <div style={{ marginTop: '0.4rem', display: 'flex', gap: '0.5rem' }}>
            <button className="btn btn-ghost" style={small} onClick={() => locate(different, true)}>Open it anyway</button>
            <button className="btn btn-ghost" style={small} onClick={() => setDifferent(null)}>Cancel</button>
          </div>
        </div>
      )}

      <div style={{ marginTop: '2rem', borderTop: '1px solid var(--border)', paddingTop: '1rem' }}>
        {!startNew ? (
          <button type="button" onClick={() => setStartNew(true)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', textDecoration: 'underline', fontFamily: 'inherit' }}>
            Start a new, empty archive instead…
          </button>
        ) : (
          <div>
            <p style={muted}><strong>Start a new, empty archive?</strong> Your existing archive is not deleted:
              you can open it again later with Settings → Open a different archive.</p>
            <ArchiveSetupStep location={null} onDone={onResolved} confirmNew />
          </div>
        )}
      </div>
    </div>
  )
}

export function ArchiveMovingScreen() {
  return (
    <div style={{ padding: '3rem 2rem', maxWidth: '640px' }}>
      <h1 style={{ fontFamily: 'var(--font-serif)', fontSize: '1.4rem', color: 'var(--navy)' }}>Your archive is being moved</h1>
      <p style={muted}>Searching and editing are paused until the copy has been checked. You can follow the progress in Settings.</p>
    </div>
  )
}
