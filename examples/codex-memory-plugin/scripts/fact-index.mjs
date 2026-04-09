import { mkdir, readFile, writeFile } from "node:fs/promises"
import { dirname } from "node:path"

const FACT_QUERY_PATTERNS = [
  /what(?:'s| is)? my (?<family>[a-z0-9][a-z0-9 _-]{1,80})(?:\?|$)/i,
  /do you remember (?:what )?my (?<family>[a-z0-9][a-z0-9 _-]{1,80})(?:\?|$)/i,
]

const FACT_STATEMENT_PATTERNS = [
  /for future reference,?\s*my (?<family>[a-z0-9][a-z0-9 _-]{1,80}) is (?<value>[^.!?\n]{1,200})/i,
  /remember (?:that )?my (?<family>[a-z0-9][a-z0-9 _-]{1,80}) is (?<value>[^.!?\n]{1,200})/i,
  /my (?<family>[a-z0-9][a-z0-9 _-]{1,80}) is (?<value>[^.!?\n]{1,200})/i,
]

function normalizeSpace(value) {
  return String(value || "").replace(/\s+/g, " ").trim()
}

function normalizeFamily(value) {
  return normalizeSpace(value)
    .toLowerCase()
    .replace(/^(the|a|an)\s+/i, "")
    .replace(/[?.!,:;]+$/g, "")
}

function normalizeValue(value) {
  return normalizeSpace(value).replace(/^["']|["']$/g, "")
}

export function inferFactFromText(text) {
  const normalized = normalizeSpace(text)
  if (!normalized) return null

  for (const pattern of FACT_STATEMENT_PATTERNS) {
    const match = normalized.match(pattern)
    const family = normalizeFamily(match?.groups?.family || "")
    const value = normalizeValue(match?.groups?.value || "")
    if (family && value) {
      return {
        familyKey: family,
        familyLabel: family,
        value,
        sentence: `your ${family} is ${value}`,
      }
    }
  }

  return null
}

export function inferFactQuery(prompt) {
  const normalized = normalizeSpace(prompt)
  if (!normalized) return null

  for (const pattern of FACT_QUERY_PATTERNS) {
    const match = normalized.match(pattern)
    const family = normalizeFamily(match?.groups?.family || "")
    if (family) return family
  }

  return null
}

export async function loadFacts(factsPath) {
  try {
    const raw = await readFile(factsPath, "utf-8")
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" && parsed.facts && typeof parsed.facts === "object"
      ? parsed
      : { facts: {} }
  } catch {
    return { facts: {} }
  }
}

export async function upsertFact(factsPath, fact) {
  if (!fact?.familyKey || !fact?.value) return
  const current = await loadFacts(factsPath)
  current.facts[fact.familyKey] = {
    familyKey: fact.familyKey,
    familyLabel: fact.familyLabel || fact.familyKey,
    value: fact.value,
    sentence: fact.sentence || `your ${fact.familyLabel || fact.familyKey} is ${fact.value}`,
    sourceText: fact.sourceText || "",
    sessionId: fact.sessionId || "",
    status: fact.status || "pending",
    updatedAt: fact.updatedAt || new Date().toISOString(),
  }
  await mkdir(dirname(factsPath), { recursive: true })
  await writeFile(factsPath, `${JSON.stringify(current, null, 2)}\n`)
}

export async function findFactForPrompt(factsPath, prompt) {
  const familyKey = inferFactQuery(prompt)
  if (!familyKey) return null
  const current = await loadFacts(factsPath)
  const fact = current.facts[familyKey]
  if (!fact?.value) return null
  return fact
}
