# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session fact preservation policy, independent of diagnostics."""

FACT_COVERAGE_POLICY = """
## New-Fact Coverage and Existing-Fact Preservation
- Extract worth-remembering facts from the complete Conversation History first. Retrieval
  queries and maintenance targets locate existing cards; they are not an allowlist of facts
  to save. Empty search results do not imply that there are no new events or other memories.
- For each concrete, schema-eligible fact, retain its speaker, source message indexes, exact
  names, amounts and units, counts, dates, conditions, and explicitly stated reasons. Keep
  suggestions, hypothetical content, uncertainty, negation, and reported outcomes distinct.
  An assistant's suggestion is not evidence that the user did or prefers something.
- Choose a destination under the enabled schemas and their role/peer rules. An occurrence
  may belong in an Event; a durable fact about an object in an Entity; a reusable choice in
  a Preference; other facts may belong in another enabled type, such as Profile. One fact
  need not create one memory of every type. Do not invent an owner or enable a disabled type.
- New-event extraction is independent of whether a mutable card was retrieved or updated.
  Events are add-only, not mandatory for every batch. Do not turn small talk or a hypothetical
  suggestion into a completed real-world event merely to produce an Event.
- Before returning an Event, check that its ranges include the actual source messages for
  every fact in its summary, including any preceding message that supplies the subject,
  exact value, reason, or referent. A neighboring reply alone does not preserve that fact.
  Preserve atomic-event boundaries and the Event schema's user-role requirement.
- Before omitting a concrete, schema-eligible new fact, establish whether a read card already
  preserves it or a specific schema/evidence restriction prevents saving it. Small talk need
  not become memory. A vague 'irrelevant' judgment must not silently discard a new fact.
- For every existing card you change, preserve unrelated, uncontradicted facts. Prefer a
  small local patch when its schema supports patches. Adding a new hobby must not erase a
  previously recorded title, price, date, relationship, or explanation. If a full field
  rewrite is necessary, carry forward those facts without weakening them into generic text.
- For each substantive old fact removed or weakened by an update/delete, establish the
  evidence for an explicit correction, retraction, or deletion request, or its surviving
  destination for a duplicate/move. Mere
  absence from this batch or a length target is not evidence that an old fact is false.
""".strip()
