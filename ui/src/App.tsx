import { useState, useCallback } from 'react'
import * as XLSX from 'xlsx'
import './App.css'

interface RowData {
  asin: string
  style: string
}

function normalizeValue(val: unknown): string {
  if (val === null || val === undefined) return ''
  return String(val).trim()
}

function findColumn(headers: string[], candidates: string[]): number {
  for (const candidate of candidates) {
    const idx = headers.findIndex(
      (h) => h.toLowerCase().replace(/[^a-z0-9]/g, '') === candidate
    )
    if (idx !== -1) return idx
  }
  return -1
}

function App() {
  const [rows, setRows] = useState<RowData[]>([])
  const [fileName, setFileName] = useState<string>('')
  const [error, setError] = useState<string>('')
  const [dragOver, setDragOver] = useState(false)

  const processFile = useCallback((file: File) => {
    setError('')
    setRows([])
    setFileName(file.name)

    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const data = new Uint8Array(e.target?.result as ArrayBuffer)
        const workbook = XLSX.read(data, { type: 'array' })
        const sheet = workbook.Sheets[workbook.SheetNames[0]]
        const json = XLSX.utils.sheet_to_json<string[]>(sheet, { header: 1 })

        if (json.length < 2) {
          setError('Spreadsheet appears empty or has no data rows.')
          return
        }

        const headers = (json[0] as string[]).map((h) =>
          String(h ?? '').trim()
        )

        const asinCol = findColumn(headers, ['asin'])
        const styleCol = findColumn(headers, [
          'style',
          'styleno',
          'stylenumber',
          'stylenum',
        ])

        if (asinCol === -1) {
          setError(
            `Could not find an "ASIN" column. Found columns: ${headers.join(', ')}`
          )
          return
        }
        if (styleCol === -1) {
          setError(
            `Could not find a "Style #" column. Found columns: ${headers.join(', ')}`
          )
          return
        }

        const parsed: RowData[] = []
        for (let i = 1; i < json.length; i++) {
          const row = json[i] as unknown[]
          if (!row || row.length === 0) continue

          const asin = normalizeValue(row[asinCol])
          const style = normalizeValue(row[styleCol])

          // Skip completely empty rows
          if (!asin && !style) continue

          parsed.push({ asin, style })
        }

        if (parsed.length === 0) {
          setError('No data rows found after the header.')
          return
        }

        setRows(parsed)
      } catch {
        setError('Failed to parse the spreadsheet. Make sure it is a valid .xlsx or .csv file.')
      }
    }
    reader.readAsArrayBuffer(file)
  }, [])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) processFile(file)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files?.[0]
    if (file) processFile(file)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(true)
  }

  const handleDragLeave = () => setDragOver(false)

  return (
    <div className="app">
      <h1>Renameinator</h1>
      <p className="subtitle">Upload a spreadsheet with ASIN and Style # columns</p>

      <div
        className={`dropzone ${dragOver ? 'dragover' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => document.getElementById('file-input')?.click()}
      >
        <input
          id="file-input"
          type="file"
          accept=".xlsx,.xls,.csv"
          onChange={handleFileChange}
          hidden
        />
        <div className="dropzone-content">
          <span className="upload-icon">+</span>
          <p>Drag & drop your spreadsheet here, or click to browse</p>
          <p className="file-types">.xlsx, .xls, .csv</p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {fileName && rows.length > 0 && (
        <div className="results">
          <div className="results-header">
            <span className="file-label">{fileName}</span>
            <span className="row-count">{rows.length} rows</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>ASIN</th>
                <th>Style #</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  <td className="row-num">{i + 1}</td>
                  <td>{row.asin || <span className="empty">—</span>}</td>
                  <td>{row.style || <span className="empty">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default App
