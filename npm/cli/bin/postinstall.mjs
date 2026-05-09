#!/usr/bin/env node
process.stderr.write(`
  ╔═══════════════════════════════════════════════════╗
  ║            OpenViking CLI installed               ║
  ╚═══════════════════════════════════════════════════╝

  Usage:   ov <command> [options]

  Commands:
    ov health              Check server connectivity
    ov search "query"      Context-aware semantic search
    ov ls                  List directory contents
    ov read <uri>          Read full file content
    ov add-resource <path> Add files or URLs
    ov add-memory "text"   Store a memory
    ov config show         Show current configuration

  Run "ov --help" for the full command list.
\n`);
