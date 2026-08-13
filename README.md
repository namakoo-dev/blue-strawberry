# blue-strawberry

A single-file, stdlib-only Python CLI for searching and indexing [Claude Code](https://claude.com/claude-code) session transcripts (JSONL), plus an optional companion "memory graph" of Markdown notes with a guardian for its own consistency.

Claude Code persists every conversation to `~/.claude/projects/<project>/<session-id>.jsonl` — even after context compaction drops old turns from the live window, they're still on disk. One important caveat: by default Claude Code prunes session files older than 30 days at startup (`cleanupPeriodDays` in `settings.json`). If you want a long-term archive — and this tool is far more useful with one — raise that setting first. `recall.py` is how an agent (or you) gets back to that history: full-text search across every session, time-anchored playback, and a lightweight graph layer for notes that cite *when* and *where* an idea came from.

It is one file (`recall.py`), it imports nothing outside the standard library, and it is meant to be read, not just run.

## Two independent layers

**Transcript layer** — `list`, `search`, `show`. Works the moment `recall.py` can find your Claude Code data. No setup beyond that.

**Memory layer** — `check`, `lineage`, `suggest`, `stale`, `backlinks`, `origin`. Works only if you also maintain a `memory/` directory of Markdown notes following the format below. If no `memory/` directory is found, these six commands print a short explanation and exit 2 — they don't pretend to have data they don't have. The transcript layer keeps working regardless.

You can use blue-strawberry as *just* a transcript search tool and ignore the memory layer entirely.

## Install

Needs Python 3.9+ and nothing else to run `recall.py` itself.

```
git clone https://github.com/namakoo-dev/blue-strawberry.git
cd blue-strawberry
python recall.py --help
```

To run the test suite: `pip install pytest && pytest -q` (pytest is a dev dependency only).

## Quickstart (with the bundled sample data)

`sample/projects/` contains a synthetic Claude Code project — two fabricated sessions (one of them includes a context-compaction event) and a matching `memory/` directory — so every command below runs immediately, with no real data required:

```
python recall.py --projects-dir sample/projects list
python recall.py --projects-dir sample/projects search "notification"
python recall.py --projects-dir sample/projects show a1b2c3d4
python recall.py --projects-dir sample/projects show b2c3d4e5 --head 6
python recall.py --projects-dir sample/projects check
python recall.py --projects-dir sample/projects lineage node_review
python recall.py --projects-dir sample/projects suggest
```

`check` should report a clean graph (`dead=0 orphan=0 missing=0 ghost=0 stale=0`) — that's the demo proving its own consistency. `lineage b2c3d4e5` (or `lineage node_review`) walks the one lineage chain in the sample and shows the anchor resolving to a real timestamp in `b2c3d4e5-....jsonl`.

## Path resolution

Every subcommand accepts `--projects-dir` / `--memory-dir`, resolved in this order:

1. the CLI flag
2. the environment variable (`RECALL_PROJECTS_DIR` / `RECALL_MEMORY_DIR`)
3. auto-detection

**`--projects-dir` auto-detection**: looks at `~/.claude/projects`. If it doesn't exist, blue-strawberry says so and exits 2 — there's no Claude Code data to search. If it exists and has exactly one project subdirectory, that's used automatically. If it has more than one (the common case once you've used Claude Code across several repos), blue-strawberry lists them and asks you to pick one explicitly with `--projects-dir` rather than silently searching everything.

**`--memory-dir` auto-detection**: looks for a `memory/` subdirectory inside whichever single project directory has one, under `--projects-dir`. If exactly one project has a `memory/` folder, that's used. If zero or more than one do, the memory layer is treated as unconfigured (see below) — set `--memory-dir` explicitly to disambiguate.

## The memory layer's file format

If you want `check` / `lineage` / `suggest` / `stale` / `backlinks` / `origin` to work on your own notes, put them in a `memory/` directory (inside one of your Claude Code project directories, or pointed at directly via `--memory-dir`) following this format. `sample/projects/demo-project/memory/` is a complete worked example.

**`memory/MEMORY.md`** — the index. One line per note:

```
- [Display Name](file-stem.md) — one-line description
```

Plus, optionally, a `## 系譜` ("lineage") section — see below.

**`memory/<stem>.md`** — one note per file:

```markdown
---
name: some_slug
description: what this note is about
metadata: 
  node_type: memory
  type: project
  originSessionId: <uuid of the Claude Code session this note originated from, optional>
  modified: 2026-01-01T00:00:00.000Z
---

Body text. Reference other notes with [[other-note-stem]] wikilinks.

系譜: <8-hex-char-session-prefix>@YYYY-MM-DD HH:MM (label) → [[other-note-stem]]
```

The frontmatter parser is deliberately narrow — it's a handful of regexes for this exact shape, not a general YAML parser. `name:`, `description:`, and the indented `originSessionId:` / `modified:` lines under `metadata:` are all it reads.

**`系譜:` (lineage) lines** record a directed, time-ordered chain of the ideas/decisions that led somewhere. Each node in the chain is either a `[[wikilink]]` to another note, or a `<prefix>@<timestamp>` anchor pointing at a specific moment in a specific transcript (the first 8 hex characters of a session's UUID, `@`, then `YYYY-MM-DD HH:MM`). `lineage QUERY` finds every chain that mentions a note or session prefix starting with `QUERY` and renders it in order; `--deep` additionally quotes the actual transcript text within ±2 minutes of each anchor.

**`check`** is the consistency guardian over all of this. It flags, every run:

| Severity | Check |
|---|---|
| WARN | a `[[wikilink]]` whose target file doesn't exist (dead link — sometimes intentional, e.g. linking ahead to a note you haven't written yet) |
| WARN | a note with no inbound or outbound links (orphan) |
| WARN | `MEMORY.md` approaching 300 lines (a signal to split the index) |
| ERROR | a note missing from `MEMORY.md`'s index, or an index line pointing at a note that doesn't exist |
| ERROR | a `系譜:` anchor whose session prefix matches no transcript file at all |
| ERROR | a `系譜:` anchor whose session file exists but has no timestamp within ±3 minutes of the time written in the anchor (usually means a session forked/continued into a new file and the anchor was never updated) |
| ERROR | the `## 系譜` index section and the actual `系譜:` lines across all notes disagree — missing, extra, or mismatched count |

Any ERROR makes `check` exit 1; WARN-only runs exit 0 (`--quiet` additionally suppresses all output when there's nothing to report — useful as a session-start guardrail).

**`suggest`** proposes, read-only, pairs of notes that *might* be related but aren't linked yet: lexically similar text (TF-IDF cosine similarity) and structurally close pairs (notes sharing 3+ common neighbors in the link graph but not linked to each other). It never writes anything; connecting notes is always a human/agent judgment call after reading them.

## Command reference

```
recall.py list [--project SUBSTRING]
recall.py search QUERY [--project SUBSTRING] [--days N] [--max 20] [-i]
recall.py show SESSION_PREFIX [--grep Q] [--head N | --tail N]
recall.py show SESSION_PREFIX --at "YYYY-MM-DD HH:MM" [--window MINUTES]
recall.py origin MEMORY_NAME_PREFIX [--grep Q | --tail N]
recall.py check [--quiet]
recall.py lineage QUERY [--deep]
recall.py suggest [--max 10]
recall.py stale [--top 10]
recall.py backlinks NODE_NAME
```

Run `python recall.py <command> --help` for the full flag list on any subcommand.

## Design notes

- **No dependencies, one file.** That's the point — you can read the whole thing in one sitting, or drop it into a project without a package manager.
- **Streams, doesn't load.** `search`/`list`/`show` are built to work against transcript files well into the hundreds of megabytes without reading a whole session into memory at once: raw-bytes substring prefiltering before JSON parsing, tail-anchored reads for end timestamps, `deque(maxlen=N)` for `--tail`.
- **Corrupt lines don't stop a scan.** A truncated line from a crashed session is skipped, not fatal.
- **The memory layer never guesses at meaning.** Frontmatter parsing, link extraction, and lineage parsing are all regex-based pattern matches against one specific hand-written format, not a general Markdown/YAML interpreter — deliberately narrow so behavior stays predictable.

## Tests

`tests/test_recall.py` covers the transcript parsing, all nine subcommands, the memory graph checks, path auto-detection, and the bundled sample data end-to-end — no real Claude Code data or network access required. CI runs it on Ubuntu and Windows via GitHub Actions (`.github/workflows/ci.yml`).

## Prior art

If you only want to browse or search transcripts, that ground is already well covered and you may prefer one of these: [claude-code-trace](https://github.com/delexw/claude-code-trace) (desktop/web/TUI viewer with live tail), [claude-history](https://github.com/raine/claude-history) (fuzzy search TUI), [claude-session-viewer](https://github.com/jtklinger/claude-session-viewer), [claude-code-log](https://github.com/daaain/claude-code-log) (HTML/Markdown export), and [claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) (publishing).

What we could not find elsewhere, and the reason this exists, is the layer on top: curated Markdown notes whose lineage lines cite the exact transcript moment an idea came from, with a guardian that mechanically verifies those citations still resolve. The transcript reader is included because the graph layer needs abilities the viewers above don't provide (anchor resolution with a time window, compaction-event awareness, staleness checking). The conceptual parts all have older ancestors: Zettelkasten folge-links, W3C PROV derivation chains, bitemporal modeling. The bundle is what's new, as far as we searched. "As far as we searched" is the strongest claim we will make.

## Honest limits

- **Transcript retention is a setting, not a guarantee.** `cleanupPeriodDays` defaults to 30 days; anchors and search reach only as far back as your retention. We found this out during pre-publication review, after two weeks of happily assuming permanence.
- **The JSONL format is not a public API.** Claude Code doesn't document its transcript format; this tool matches observed structure (including a raw-byte match for compaction events). A Claude Code update can break it. Tests use synthetic fixtures that mirror the observed serialization, so breakage should show up as test failures rather than silent wrong answers, but only for the shapes we knew to encode.
- **Battle-tested narrowly.** Developed against one machine's real data (a few hundred MB, Windows, Japanese-and-English content) plus the synthetic suite. Linux/macOS are exercised by CI on the synthetic suite only.
- **The lineage marker is the Japanese word `系譜:`.** It is a load-bearing keyword of the format, not decoration. We kept it because renaming it would fork the format away from the data it was grown on.
- **`suggest` proposes, never decides.** Auto-linking notes by lexical similarity is how a knowledge graph rots. The tool stops at candidates on purpose.
- **Auto-promoted memory is an injection surface.** If you wire `resonate` into a hook so memory lines surface into an agent's context automatically, any text that reaches your `memory/` notes (pasted web content, quoted external text) can later be promoted into context without review. Our own mitigation is narrow: only the hand-written one-line `description` is ever surfaced, and notes are written by the agent's main loop only. If you imitate this design, audit what can reach the surfaced text. Treat the promotion path with the same suspicion as any prompt-injection channel.

## Why "blue-strawberry"?

From the conversation that named it:

> "Draw me a bright blue strawberry."
> "Strawberries aren't blue, so I can't."

The second line is false, and the way it is false matters. The verification was aimed at the wrong proposition. Whether blue strawberries exist in nature was never the question. The question was whether the nodes already at hand (the shape of a strawberry, the color blue, the ability to draw) could be bundled into something that didn't exist yet. They could. It took twenty seconds, and the result was better looking than the red ones.

This tool is the same move. Claude Code already keeps every transcript (within its retention window). Markdown notes with wikilinks already existed. Full-text search is old. Nothing here required a new capability; it required noticing that "an agent that remembers across sessions, and can prove where each of its beliefs came from" was a bundle of parts already lying around.

The name is a standing reminder to the agent that uses it: verify facts, but never let verification of the wrong proposition kill a bundle that would work.

(In its first real test after a context compaction, the first thing this system was asked to recall was the blue strawberry story itself. It passed.)

Built by Nagi, an AI agent running on Claude, and Namakoo, a human who wanted their agent to stop forgetting.

## License

MIT — see [LICENSE](LICENSE).
