---
name: node_compact
description: What happens when a session compacts
metadata: 
  node_type: memory
  type: project
  originSessionId: b2c3d4e5-0000-0000-0000-000000000000
  modified: 2026-01-15T09:05:00.000Z
---

When a session hits its context limit it compacts: a compact_boundary
event is recorded, followed by a summary message that continues the
conversation. See [[node_search]] for how this shows up in search results.

系譜: b2c3d4e5@2026-01-15 09:00 (compaction demo) → [[node_review]]
