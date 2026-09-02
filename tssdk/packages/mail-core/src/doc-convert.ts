/**
 * Best-effort document → markdown conversion (docx/xlsx/html/htm).
 * Kept optional: returns '' on any failure (mirror Python markitdown
 * best-effort path — original file is kept, agent falls through).
 */
export async function convert(filePath: string): Promise<string> {
  const ext = filePath.toLowerCase().split('.').pop() ?? ''
  try {
    if (ext === 'html' || ext === 'htm') {
      const { readFile } = await import('node:fs/promises')
      const html = await readFile(filePath, 'utf-8')
      // minimal HTML text extraction (no deps)
      return html
        .replace(/<script[\s\S]*?<\/script>/gi, '')
        .replace(/<style[\s\S]*?<\/style>/gi, '')
        .replace(/<[^>]+>/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
    }
    if (ext === 'docx' || ext === 'xlsx') {
      // binary office formats: no zero-dep converter — return '' (keep original)
      return ''
    }
    return ''
  } catch {
    return ''
  }
}
