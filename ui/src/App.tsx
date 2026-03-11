import { useState, useCallback } from 'react'
import * as XLSX from 'xlsx'
import { parseSpreadsheet, matchImages } from './renameEngine'
import { buildZip } from './zipBuilder'
import type { SpreadsheetRow, MatchResult } from './renameEngine'
import './App.css'

function App() {
  const [fileName, setFileName] = useState('')
  const [asinRows, setAsinRows] = useState<SpreadsheetRow[]>([])
  const [imgRows, setImgRows] = useState<SpreadsheetRow[]>([])
  const [images, setImages] = useState<File[]>([])
  const [matches, setMatches] = useState<MatchResult[]>([])
  const [error, setError] = useState('')
  const [dragOverSheet, setDragOverSheet] = useState(false)
  const [dragOverImages, setDragOverImages] = useState(false)
  const [processing, setProcessing] = useState(false)

  // ---- Spreadsheet upload ----
  const processSpreadsheet = useCallback((file: File) => {
    setError('')
    setFileName(file.name)
    setMatches([])

    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const data = new Uint8Array(e.target?.result as ArrayBuffer)
        const workbook = XLSX.read(data, { type: 'array' })

        // Use the first sheet, or "USE" sheet if it exists
        const sheetName = workbook.SheetNames.includes('USE')
          ? 'USE'
          : workbook.SheetNames[0]
        const sheet = workbook.Sheets[sheetName]
        const json = XLSX.utils.sheet_to_json<unknown[]>(sheet, { header: 1 })

        if (json.length < 2) {
          setError('Spreadsheet appears empty.')
          return
        }

        const headers = (json[0] as string[]).map((h) => String(h ?? '').trim())
        const dataRows = json.slice(1) as unknown[][]

        const { asinRows: parsedAsinRows, imgRows: parsedImgRows } =
          parseSpreadsheet(headers, dataRows)

        if (parsedAsinRows.length === 0) {
          setError(
            `No ASIN assignments found. Need "ASIN" and "ASIN STYLE" columns.\nFound: ${headers.join(', ')}`
          )
          return
        }

        setAsinRows(parsedAsinRows)
        setImgRows(parsedImgRows)
      } catch {
        setError('Failed to parse spreadsheet. Make sure it is a valid .xlsx or .csv file.')
      }
    }
    reader.readAsArrayBuffer(file)
  }, [])

  // ---- Image upload ----
  const handleImageUpload = useCallback(
    (files: FileList) => {
      const imageFiles = Array.from(files).filter((f) =>
        /\.(jpe?g|png|gif|bmp|tiff?|webp)$/i.test(f.name)
      )
      const newImages = [...images, ...imageFiles]
      setImages(newImages)

      // Auto-match if we have spreadsheet data
      if (asinRows.length > 0) {
        const results = matchImages(newImages, asinRows, imgRows)
        setMatches(results)
      }
    },
    [images, asinRows, imgRows]
  )

  // ---- Download ZIP ----
  const handleDownload = async () => {
    const matched = matches.filter((m) => m.matched)
    if (matched.length === 0) return

    setProcessing(true)
    try {
      const blob = await buildZip(matches)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'renamed-images.zip'
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      setError('Failed to create ZIP file.')
    }
    setProcessing(false)
  }

  const matchedCount = matches.filter((m) => m.matched).length
  const unmatchedCount = matches.filter((m) => !m.matched).length

  return (
    <div className="app">
      <h1>Renameinator</h1>

      {/* Step 1: Spreadsheet */}
      <div className="step">
        <h2>1. Upload Spreadsheet</h2>
        <p className="hint">Excel file with ASIN and ASIN STYLE columns</p>
        <div
          className={`dropzone ${dragOverSheet ? 'dragover' : ''}`}
          onDrop={(e) => {
            e.preventDefault()
            setDragOverSheet(false)
            const file = e.dataTransfer.files?.[0]
            if (file) processSpreadsheet(file)
          }}
          onDragOver={(e) => { e.preventDefault(); setDragOverSheet(true) }}
          onDragLeave={() => setDragOverSheet(false)}
          onClick={() => document.getElementById('sheet-input')?.click()}
        >
          <input
            id="sheet-input"
            type="file"
            accept=".xlsx,.xls,.csv"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) processSpreadsheet(file)
            }}
            hidden
          />
          <span className="upload-icon">+</span>
          {fileName ? (
            <p><strong>{fileName}</strong> — {asinRows.length} ASINs, {imgRows.length} image templates</p>
          ) : (
            <p>Drag & drop spreadsheet, or click to browse</p>
          )}
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {/* Step 2: Images */}
      {asinRows.length > 0 && (
        <div className="step">
          <h2>2. Upload Images</h2>
          <p className="hint">Drag & drop image files to match against the spreadsheet</p>
          <div
            className={`dropzone ${dragOverImages ? 'dragover' : ''}`}
            onDrop={(e) => {
              e.preventDefault()
              setDragOverImages(false)
              if (e.dataTransfer.files) handleImageUpload(e.dataTransfer.files)
            }}
            onDragOver={(e) => { e.preventDefault(); setDragOverImages(true) }}
            onDragLeave={() => setDragOverImages(false)}
            onClick={() => document.getElementById('image-input')?.click()}
          >
            <input
              id="image-input"
              type="file"
              accept="image/*"
              multiple
              onChange={(e) => {
                if (e.target.files) handleImageUpload(e.target.files)
              }}
              hidden
            />
            <span className="upload-icon">+</span>
            {images.length > 0 ? (
              <p><strong>{images.length} image(s) uploaded</strong></p>
            ) : (
              <p>Drag & drop images, or click to browse</p>
            )}
          </div>
          {images.length > 0 && (
            <button
              className="clear-btn"
              onClick={() => { setImages([]); setMatches([]) }}
            >
              Clear images
            </button>
          )}
        </div>
      )}

      {/* Step 3: Preview matches */}
      {matches.length > 0 && (
        <div className="step">
          <h2>3. Preview Renames</h2>
          <div className="match-summary">
            <span className="matched">{matchedCount} matched</span>
            {unmatchedCount > 0 && (
              <span className="unmatched">{unmatchedCount} unmatched</span>
            )}
          </div>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Original Filename</th>
                  <th>Style</th>
                  <th>ASIN</th>
                  <th>Suffix</th>
                  <th>New Filename</th>
                </tr>
              </thead>
              <tbody>
                {matches.map((m, i) => (
                  <tr key={i} className={m.matched ? '' : 'row-unmatched'}>
                    <td>{m.originalName}</td>
                    <td>{m.style || '—'}</td>
                    <td>{m.asin || '—'}</td>
                    <td>{m.suffix || '—'}</td>
                    <td>{m.newName || <span className="empty">No match</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Step 4: Download */}
      {matchedCount > 0 && (
        <div className="step download-step">
          <button
            className="download-btn"
            onClick={handleDownload}
            disabled={processing}
          >
            {processing ? 'Creating ZIP...' : `Download ${matchedCount} Renamed Images`}
          </button>
        </div>
      )}
    </div>
  )
}

export default App
