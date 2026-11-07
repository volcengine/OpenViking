import { test } from "node:test"
import assert from "node:assert/strict"

import { repoAllowed } from "../lib/repo-context.mjs"

test("empty allow-list keeps legacy behavior: everything allowed", () => {
  assert.equal(repoAllowed({ uri: "viking://resources/docs/OpenViking/" }, []), true)
})

test("exact name allow-list matches exact, rejects siblings", () => {
  const allowed = ["OpenViking"]
  assert.equal(repoAllowed({ uri: "viking://resources/OpenViking/" }, allowed), true)
  assert.equal(repoAllowed({ uri: "viking://resources/webbeds/" }, allowed), false)
})

test("`*` matches within one path segment, `**` crosses", () => {
  const allowed = ["Leaflet/**", "docs/*", "moo"]
  assert.equal(repoAllowed({ uri: "viking://resources/Leaflet/Leaflet/" }, allowed), true)
  assert.equal(repoAllowed({ uri: "viking://resources/docs/webbeds/" }, allowed), true)
  assert.equal(repoAllowed({ uri: "viking://resources/docs/deep/nested/" }, allowed), false)
  assert.equal(repoAllowed({ uri: "viking://resources/moo/" }, allowed), true)
})

test("non-resource uri rejected once allow-list is set", () => {
  assert.equal(repoAllowed({ uri: "viking://memories/" }, ["OpenViking"]), false)
})
