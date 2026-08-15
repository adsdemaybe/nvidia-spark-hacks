/**
 * Edit blocks — the designer revises by patch, not by rewriting the file.
 *
 * The plan always specified the designer's output as "HDL revision, **as a diff**"
 * (agent roster, §2). This is that, and it stopped being a nicety the moment a real
 * model was measured against a real context window: revising the rover meant ~9.8k
 * tokens of prompt plus ~4.6k tokens to re-emit a file that was already in the prompt —
 * 14.3k of a 16,384-token budget, before a reasoning model thinks at all. The revision
 * came back truncated, and a half-written file reaches the linter as a syntax error
 * rather than as the transport failure it is.
 *
 * A patch costs a few hundred tokens instead of forty-five hundred, and it has a second
 * benefit that matters more: **an edit that does not apply is caught here, exactly, and
 * named.** A full-file rewrite silently drops whatever the model forgot to copy — the
 * kind of regression the work order explicitly warns against — and nothing detects it.
 * A SEARCH block that matches zero times, or more than once, is a hard error with the
 * offending text quoted.
 *
 * Format, chosen because it is unambiguous and needs no line numbers (which models get
 * wrong and which shift under every prior edit):
 *
 *     <<<<<<< SEARCH
 *     the exact existing lines
 *     =======
 *     what they become
 *     >>>>>>> REPLACE
 */

export interface EditBlock {
  search: string
  replace: string
}

const BLOCK =
  /<{5,9} *SEARCH[^\n]*\n([\s\S]*?)\n={5,9}[^\n]*\n([\s\S]*?)\n>{5,9} *REPLACE/g

/** Pull every edit block out of a reply, ignoring prose and reasoning around them. */
export function parseEditBlocks(raw: string): EditBlock[] {
  const cleaned = raw
    .replace(/<think>[\s\S]*?<\/think>/g, "")
    .replace(/^[\s\S]*?<\/think>/, "")

  const blocks: EditBlock[] = []
  BLOCK.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = BLOCK.exec(cleaned))) {
    blocks.push({ search: m[1], replace: m[2] })
  }
  return blocks
}

/**
 * A reply that opened an edit block and never closed it is truncated, not empty.
 * Distinguishing the two decides whether the loop retries or reports a design failure.
 */
export function looksTruncated(raw: string): boolean {
  const opens = (raw.match(/<{5,9} *SEARCH/g) ?? []).length
  const closes = (raw.match(/>{5,9} *REPLACE/g) ?? []).length
  return opens > closes
}

export interface PatchResult {
  code: string
  applied: number
  notes: string[]
}

/**
 * Apply edits in order, each against the result of the last.
 *
 * Order matters and is the model's to choose: a later block may legitimately target
 * text an earlier block produced. What is *not* negotiable is that every block applies
 * exactly once — anything else means the model is editing a file it has misremembered,
 * and continuing would corrupt the design quietly.
 */
export function applyEditBlocks(original: string, edits: EditBlock[]): PatchResult {
  if (!edits.length) throw new Error("no edit blocks found in the reply")

  let code = original
  const notes: string[] = []

  edits.forEach((edit, i) => {
    const label = `edit ${i + 1}/${edits.length}`

    if (!edit.search.trim()) {
      throw new Error(`${label}: empty SEARCH block — refusing to guess where it goes`)
    }

    let count = occurrences(code, edit.search)
    let search = edit.search

    // Models reproduce indentation and trailing whitespace imperfectly. One retry with
    // whitespace normalised is worth it; anything looser starts matching the wrong
    // place, which is the failure mode this whole function exists to prevent.
    if (count === 0) {
      const relaxed = findRelaxed(code, edit.search)
      if (relaxed) {
        search = relaxed
        count = occurrences(code, search)
        notes.push(`${label}: matched after normalising whitespace`)
      }
    }

    if (count === 0) {
      throw new Error(
        `${label}: SEARCH text does not appear in the current HDL. The revision was ` +
          `written against a file that does not match what is on disk.\n` +
          `--- searched for ---\n${firstLines(edit.search, 6)}`,
      )
    }
    if (count > 1) {
      throw new Error(
        `${label}: SEARCH text appears ${count} times — ambiguous. Include enough ` +
          `surrounding context to make it unique.\n` +
          `--- searched for ---\n${firstLines(edit.search, 6)}`,
      )
    }

    code = code.replace(search, () => edit.replace)
  })

  return { code, applied: edits.length, notes }
}

function occurrences(haystack: string, needle: string): number {
  if (!needle) return 0
  let count = 0
  let from = 0
  for (;;) {
    const at = haystack.indexOf(needle, from)
    if (at === -1) return count
    count++
    from = at + needle.length
  }
}

/**
 * Locate the search text ignoring per-line leading/trailing whitespace, and return the
 * *actual* substring from the file so the replacement still splices cleanly.
 */
function findRelaxed(code: string, search: string): string | null {
  const wanted = search.split("\n").map((l) => l.trim())
  const lines = code.split("\n")
  for (let i = 0; i + wanted.length <= lines.length; i++) {
    let hit = true
    for (let j = 0; j < wanted.length; j++) {
      if (lines[i + j].trim() !== wanted[j]) {
        hit = false
        break
      }
    }
    if (hit) return lines.slice(i, i + wanted.length).join("\n")
  }
  return null
}

function firstLines(text: string, n: number): string {
  const lines = text.split("\n")
  return lines.slice(0, n).join("\n") + (lines.length > n ? `\n… (${lines.length - n} more)` : "")
}

export const EDIT_BLOCK_INSTRUCTIONS = `
Return your revision as edit blocks, not as a whole file. Each block is:

<<<<<<< SEARCH
(exact lines from the current HDL, copied character for character)
=======
(what those lines become)
>>>>>>> REPLACE

Rules:
- Copy the SEARCH text exactly as it appears, including indentation and comments.
- Include enough surrounding lines that the SEARCH text appears exactly once in the file.
- One block per change. Emit as many blocks as the work order needs.
- To delete something, leave the REPLACE side empty.
- To add something new, SEARCH for the line it goes after and repeat it in REPLACE
  followed by the new lines.
- Change nothing the work order did not ask for.
Output only edit blocks. No prose before or after.
`.trim()
