# Export an OV conversation for a host handoff

Use this when an older conversation relied on OV to supply its history and the
host no longer has that history. Stop writes to that OV session before exporting.
The installed Python SDK and its normal authentication configuration are used.

```sh
python examples/session-history-export/export_history.py SESSION_ID ./handoff --url http://localhost:1933
```

The exporter enumerates archive raw messages (including pending/failed archives)
and active messages through the supported filesystem APIs. It preserves message
order, deduplicates identical message IDs, reads separated tool outputs, checks
OV attachment references, and rejects conflicting copies or a changing source.
It reads the source twice; callers must still stop concurrent writers for a
consistent handoff. It does not rely on `/context`, which can omit older history.

- `history.json`: full captured source, metadata, and external tool-output text.
- `handoff.md`: conversation data for a new host session's supported file input.
- `manifest.json`: checksum and `exported_not_imported` status, written last.

Repeating an unchanged export to the same directory is safe. Use a different
directory for a changed snapshot. No source data is deleted or changed. Attachment
references are checked, not copied as binary attachments; provide those files to
the target host separately where needed. OV capture may already have filtered or
truncated source messages, so this is not guaranteed to reproduce the original
host transcript.

Create a new host session and attach `handoff.md` and `history.json` using that
host's supported file/context input. Have the host summarize the material if it
exceeds its window. Check the last user request, unresolved actions, tool
call/result relationships and required attachments before continuing. If they
cannot be verified, retain the old conversation and resolve the missing material
first. This tool does not import into private JSONL formats, certify a completed
migration, or switch the plugin mode automatically.
