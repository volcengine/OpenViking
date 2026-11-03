# Built-in parsers

OpenViking routes resources to built-in parsers or the configured external parser API. Applications add resources through
`add_resource`; parser registries are internal implementation details.

For the complete Accessor, Parser, Understanding, Connector, and asynchronous execution
flow, see [Resource processing](../../../docs/en/api/02-resources.md#resource-processing-pipeline).

Each built-in parser implements `BaseParser` and returns a `ParseResult`. New formats are
added to the repository as built-in parsers and registered in `openviking/parse/registry.py`.
