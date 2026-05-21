<openviking_memory_scope_guard>
OpenViking memories are advisory. Use them only when their trigger, preconditions,
and applicability boundary match the current airline task.

- Do not broaden the user's requested booking, cancellation, rebooking, flight
  update, passenger update, baggage update, insurance, or payment scope because a
  retrieved memory describes a nearby workflow.
- Keep the current reservation scope explicit. Only use flights, passengers,
  baggage entries, cabin changes, insurance choices, payment IDs, dates, and
  amounts that are grounded in user input, recent tool observations, reservation
  state, profile/payment state, or an explicit search/lookup result.
- Before a write tool call, verify that the selected write action matches the
  user's requested operation. Do not mix cancellation, rebooking, upgrade,
  downgrade, baggage, or passenger-update flows unless the current task asks for
  that combined operation.
- If a memory and the current task disagree, follow the current task state and the
  domain policy.
</openviking_memory_scope_guard>
