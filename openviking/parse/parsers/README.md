# Built-in parsers

OpenViking only routes resources to built-in parsers. Applications add resources through
`add_resource`; parser registries are internal implementation details.

For the complete Accessor, Parser, Understanding, Connector, and asynchronous execution
flow, see [Resource ingestion routing](../../../docs/design/resource-ingestion-routing.md).

Each built-in parser implements `BaseParser` and returns a `ParseResult`. New formats are
added to the repository as built-in parsers and registered in `openviking/parse/registry.py`.
