import JSZip from 'jszip'
import type { MatchResult } from './renameEngine'

export async function buildZip(matches: MatchResult[]): Promise<Blob> {
  const zip = new JSZip()
  const matched = matches.filter((m) => m.matched)

  for (const match of matched) {
    const arrayBuffer = await match.originalFile.arrayBuffer()
    zip.file(match.newName, arrayBuffer)
  }

  return zip.generateAsync({ type: 'blob' })
}
