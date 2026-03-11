// Angle-to-suffix mapping (from Python renameinator.py)
const ANGLE_MAP: Record<string, string> = {
  front: 'MAIN',
  default: 'MAIN',
  hero: 'MAIN',
  back: 'PT01',
  side: 'PT02',
  detail: 'PT03',
  lifestyle: 'PT04',
  closeup: 'PT05',
  flat: 'PT06',
  swatch: 'PT07',
  hero2: 'PT02',
  hero3: 'PT03',
  alt1: 'PT04',
  altfront: 'PT05',
  model34: 'PT06',
  modelalt1: 'PT07',
  modelalt4: 'PT08',
  modelback: 'PT09',
  modelhood2: 'PT10',
  modelclose3: 'PT11',
  modelint: 'PT12',
}

export interface SpreadsheetRow {
  asin: string
  asinStyle: string
  imgPath: string
  imgStyleNum: string
  suffixFormula: string
}

export interface MatchResult {
  originalFile: File
  originalName: string
  asin: string
  style: string
  suffix: string
  newName: string
  matched: boolean
}

function getExtension(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot >= 0 ? filename.substring(dot) : ''
}

function getStem(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot >= 0 ? filename.substring(0, dot) : filename
}

/**
 * Extract the angle from a filename.
 * Handles patterns like:
 *   AP1D105708_AC746_Regular_Back.jpg → Back
 *   NF0A5ABT0VO-HERO.png → HERO
 */
function extractAngle(filename: string): string {
  const stem = getStem(filename)

  // Try dash-separated (TNF pattern)
  if (stem.includes('-')) {
    const parts = stem.split('-')
    return parts[parts.length - 1].replace(/\s*\(\d+\)$/, '')
  }

  // Underscore-separated
  const parts = stem.split('_')
  if (parts.length >= 2) {
    return parts[parts.length - 1]
  }

  return ''
}

function angleToSuffix(angle: string): string {
  return ANGLE_MAP[angle.toLowerCase()] || 'PT01'
}

/**
 * Extract the style portion from a filename, stripping the angle suffix.
 * e.g., AP1D105708_AC746_Regular_Back.jpg → AP1D105708_AC746
 */
function extractStyleFromFilename(filename: string): string {
  const stem = getStem(filename)

  // TNF pattern: everything before the dash
  if (stem.includes('-')) {
    return stem.split('-')[0]
  }

  // Underscore pattern: remove last 1-2 parts (angle, and optionally "Regular"/"Model")
  const parts = stem.split('_')
  if (parts.length >= 3) {
    // Check if second-to-last is a known keyword like "Regular", "Model"
    const secondLast = parts[parts.length - 2].toLowerCase()
    if (['regular', 'model', 'flat', 'ghost'].includes(secondLast)) {
      return parts.slice(0, -2).join('_')
    }
    return parts.slice(0, -1).join('_')
  }
  if (parts.length === 2) {
    return parts[0]
  }
  return stem
}

/**
 * Normalize a style string for comparison.
 * Converts dashes to underscores, strips whitespace, lowercases.
 */
function normalizeStyle(style: string): string {
  return style.replace(/-/g, '_').replace(/\s+/g, '').toLowerCase()
}

export function parseSpreadsheet(
  headers: string[],
  rows: unknown[][]
): { asinRows: SpreadsheetRow[]; imgRows: SpreadsheetRow[] } {
  const h = headers.map((s) => String(s ?? '').trim().toUpperCase())

  const asinCol = h.indexOf('ASIN')
  const asinStyleCol = h.indexOf('ASIN STYLE')
  const imgPathCol = h.indexOf('IMG PATH')
  const imgStyleCol = h.indexOf('IMG STYLE #')
  const suffixCol = h.indexOf('SUFFIX FORMULA')

  const asinRows: SpreadsheetRow[] = []
  const imgRows: SpreadsheetRow[] = []

  for (const row of rows) {
    const asin = String(row[asinCol] ?? '').trim()
    const asinStyle = asinCol >= 0 && asinStyleCol >= 0 ? String(row[asinStyleCol] ?? '').trim() : ''
    const imgPath = imgPathCol >= 0 ? String(row[imgPathCol] ?? '').trim() : ''
    const imgStyleNum = imgStyleCol >= 0 ? String(row[imgStyleCol] ?? '').trim() : ''
    const suffixFormula = suffixCol >= 0 ? String(row[suffixCol] ?? '').trim() : ''

    const entry: SpreadsheetRow = { asin, asinStyle, imgPath, imgStyleNum, suffixFormula }

    // ASIN assignment row: has ASIN and ASIN STYLE
    if (asin && asinStyle) {
      asinRows.push(entry)
    }

    // Image template row: has IMG PATH, IMG STYLE #, SUFFIX FORMULA
    if (imgPath && imgStyleNum && suffixFormula) {
      imgRows.push(entry)
    }
  }

  return { asinRows, imgRows }
}

export function matchImages(
  files: File[],
  asinRows: SpreadsheetRow[],
  imgRows: SpreadsheetRow[]
): MatchResult[] {
  // Build style → ASINs lookup from asin assignments
  const styleToAsins = new Map<string, SpreadsheetRow[]>()
  for (const row of asinRows) {
    const key = normalizeStyle(row.asinStyle)
    if (!styleToAsins.has(key)) styleToAsins.set(key, [])
    styleToAsins.get(key)!.push(row)
  }

  // Build a lookup of image templates by IMG STYLE #
  const styleToTemplates = new Map<string, SpreadsheetRow[]>()
  for (const row of imgRows) {
    const key = normalizeStyle(row.imgStyleNum)
    if (!styleToTemplates.has(key)) styleToTemplates.set(key, [])
    styleToTemplates.get(key)!.push(row)
  }

  // If we have image template rows, use the full spreadsheet-mode matching
  if (imgRows.length > 0) {
    return matchWithTemplates(files, asinRows, imgRows, styleToAsins, styleToTemplates)
  }

  // Otherwise, simple mode: match uploaded file's style to ASIN STYLE
  return matchSimple(files, styleToAsins)
}

/**
 * Full spreadsheet mode: match files against IMG STYLE # templates,
 * then look up ASINs by ASIN STYLE.
 */
function matchWithTemplates(
  files: File[],
  _asinRows: SpreadsheetRow[],
  _imgRows: SpreadsheetRow[],
  styleToAsins: Map<string, SpreadsheetRow[]>,
  styleToTemplates: Map<string, SpreadsheetRow[]>
): MatchResult[] {
  const results: MatchResult[] = []
  const usedNames = new Set<string>()

  // For each file, find a matching template by comparing the file's extracted style
  for (const file of files) {
    const fileStyle = normalizeStyle(extractStyleFromFilename(file.name))
    const ext = getExtension(file.name)
    const angle = extractAngle(file.name)
    let matched = false

    // Try matching against IMG STYLE # values
    for (const [templateStyle, templates] of styleToTemplates) {
      if (fileStyle === templateStyle || fileStyle.startsWith(templateStyle) || templateStyle.startsWith(fileStyle)) {
        // Found a matching template — now find ASINs with the same style
        const firstTemplate = templates[0]
        const asinStyleKey = normalizeStyle(firstTemplate.imgStyleNum)

        const asins = styleToAsins.get(asinStyleKey)
        if (asins) {
          for (const asinRow of asins) {
            const suffix = firstTemplate.suffixFormula.startsWith('=')
              ? angleToSuffix(angle)
              : firstTemplate.suffixFormula || angleToSuffix(angle)
            const newName = `${asinRow.asin}.${suffix}${ext}`

            if (!usedNames.has(newName)) {
              usedNames.add(newName)
              results.push({
                originalFile: file,
                originalName: file.name,
                asin: asinRow.asin,
                style: asinRow.asinStyle,
                suffix,
                newName,
                matched: true,
              })
              matched = true
            }
          }
        }
        break
      }
    }

    if (!matched) {
      // Try direct match against ASIN STYLE
      for (const [asinStyle, asins] of styleToAsins) {
        if (fileStyle === asinStyle || fileStyle.startsWith(asinStyle) || asinStyle.startsWith(fileStyle)) {
          const suffix = angleToSuffix(angle)
          for (const asinRow of asins) {
            const newName = `${asinRow.asin}.${suffix}${ext}`
            if (!usedNames.has(newName)) {
              usedNames.add(newName)
              results.push({
                originalFile: file,
                originalName: file.name,
                asin: asinRow.asin,
                style: asinRow.asinStyle,
                suffix,
                newName,
                matched: true,
              })
              matched = true
            }
          }
          break
        }
      }
    }

    if (!matched) {
      results.push({
        originalFile: file,
        originalName: file.name,
        asin: '',
        style: '',
        suffix: '',
        newName: '',
        matched: false,
      })
    }
  }

  return results
}

/**
 * Simple mode: only ASIN + ASIN STYLE columns.
 * Match file's extracted style to ASIN STYLE, derive suffix from angle.
 */
function matchSimple(
  files: File[],
  styleToAsins: Map<string, SpreadsheetRow[]>
): MatchResult[] {
  const results: MatchResult[] = []
  const usedNames = new Set<string>()

  for (const file of files) {
    const fileStyle = normalizeStyle(extractStyleFromFilename(file.name))
    const ext = getExtension(file.name)
    const angle = extractAngle(file.name)
    const suffix = angleToSuffix(angle)
    let matched = false

    for (const [asinStyle, asins] of styleToAsins) {
      if (fileStyle === asinStyle || fileStyle.startsWith(asinStyle) || asinStyle.startsWith(fileStyle)) {
        for (const asinRow of asins) {
          const newName = `${asinRow.asin}.${suffix}${ext}`
          if (!usedNames.has(newName)) {
            usedNames.add(newName)
            results.push({
              originalFile: file,
              originalName: file.name,
              asin: asinRow.asin,
              style: asinRow.asinStyle,
              suffix,
              newName,
              matched: true,
            })
            matched = true
          }
        }
        break
      }
    }

    if (!matched) {
      results.push({
        originalFile: file,
        originalName: file.name,
        asin: '',
        style: '',
        suffix: '',
        newName: '',
        matched: false,
      })
    }
  }

  return results
}
