/**
 * Where an interrupted spoken reply stops being heard. Mirrors
 * `backend/app/services/heard.py` so the faded part of the transcript is the
 * same text the assistant knows the user didn't hear.
 */
export function heardCut(content: string, heardChars: number | null | undefined): number {
  if (heardChars == null || heardChars >= content.length) return content.length
  let cut = Math.max(0, heardChars)
  if (cut > 0 && !/\s/.test(content[cut]) && !/\s/.test(content[cut - 1])) {
    // Mid-word: keep only whole words.
    const space = Math.max(content.lastIndexOf(' ', cut - 1), content.lastIndexOf('\n', cut - 1))
    cut = space >= 0 ? space + 1 : 0
  }
  return cut
}
