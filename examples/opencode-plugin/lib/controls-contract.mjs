const sessionInput = { type: "object", properties: { sessionID: { type: "string", minLength: 1 } }, required: ["sessionID"], additionalProperties: false }
const readInput = { type: "object", properties: { offline: { type: "boolean" } }, additionalProperties: false }
// JSON Schema is the portable definition accepted by both plugin hosts.
export const OPENVIKING_CONTROLS = {
  id: "openviking.controls",
  methods: {
    status: { input: readInput, output: { type: "object" } },
    doctor: { input: readInput, output: { type: "object" } },
    recall: { input: sessionInput, output: { type: "object" }, errors: { operation_failed: { type: "object" } } },
    commit: { input: sessionInput, output: { type: "object" }, errors: { operation_failed: { type: "object" } } },
  },
  events: {},
}
