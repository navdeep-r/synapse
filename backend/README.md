# Synapse v2 — Graphiti backend

A FastAPI service that turns uploaded documents into a temporal knowledge graph
using [Graphiti](https://github.com/getzep/graphiti), and serves the existing
React console. It runs with no API keys, no Docker and no external database.

## Quick start

```bash
cd backend
./run.sh                 # creates .venv with uv, starts uvicorn on 127.0.0.1:8000
```

Then, in another shell:

```bash
cd console
npm install
npm run dev              # proxies /api to localhost:8000
```

To open the console on a populated graph rather than empty states:

```bash
cd backend
.venv/bin/python scripts/seed.py --reset
```

The seed corpus is written to exercise the features the screens exist to show:
alias resolution, a superseded fact, a cardinality conflict, mixed access tags
and boilerplate the pre-ingest filter drops.

## How it fits together

```
upload ─► parse ─► chunk ─► pre-ingest filter ─► batch into episodes
                                                        │
                                              Graphiti add_episode
                                            (extract, resolve, invalidate)
                                                        │
                                     FalkorDB (graph)  +  SQLite (provenance)
```

SQLite is the metadata store: documents, chunks, episode receipts, drop logs,
curation decisions, snapshots and settings. The graph holds entities, facts and
their validity intervals. Everything the console shows as provenance is a join
between the two.

### Why these choices

**FalkorDB Lite** is embedded, so there is no Docker requirement and no service
to start. `SYNAPSE_GRAPH_BACKEND` switches to a hosted `falkordb` or `neo4j`
instance without touching anything else. Graphiti's Kuzu backend is deliberately
not offered: upstream marks it deprecated.

**The stub LLM** (`SYNAPSE_LLM_PROVIDER=stub`, the default) answers Graphiti's
prompts from deterministic heuristics rather than a model. That makes the demo
key-free, offline, instant and reproducible. Set `openai`, `ollama` or `nvidia` to
use a real model; the ontology, resolution and temporal logic are unchanged.

### Using NVIDIA NIM

```bash
uv run python scripts/check_nvidia.py   # credentials, structured output, embedding width
```

Run the preflight first. The catalog lists models that are not provisioned for a
given account and fail only at call time — `baai/bge-m3` is listed and returns 500
on every request, and several others 404.

Three things constrain the model choice:

- **Latency multiplies.** Graphiti issues roughly six LLM calls per chunk, so
  per-call latency is the dominant cost. Reasoning models are the trap:
  `z-ai/glm-5.2` is available and accurate but spends ~250s per call on its thinking
  block, and `chat_template_kwargs: {"thinking": false}` does not suppress it. One
  document would take hours. `openai/gpt-oss-20b` answers the same prompt in ~2.6s.
- **The embedder must be symmetric.** NV-EmbedQA and E5 reject a request without an
  `input_type` of query vs passage, which the OpenAI embeddings client cannot send.
  `nvidia/nemotron-3-embed-1b` takes no such parameter.
- **Embedding width is baked into the graph.** Switching provider changes it (384 →
  2048), so wipe `var/` first or the graph holds two incompatible vector sizes and
  similarity search degrades silently rather than erroring:

```bash
uv run python scripts/seed.py --reset
```

Expect roughly 60–90s per episode against a hosted model, versus milliseconds for
the stub.

## Configuration

Copy `.env.example` to `.env`. The settings that matter most:

| Variable                 | Default        | Purpose                                                            |
| ------------------------ | -------------- | ------------------------------------------------------------------ |
| `SYNAPSE_HOST`           | `127.0.0.1`    | Loopback by default. See "Security" below.                         |
| `SYNAPSE_AUTH_TOKEN`     | unset          | When set, every endpoint requires `Authorization: Bearer <token>`. |
| `SYNAPSE_LLM_PROVIDER`   | `stub`         | `stub`, `openai`, `ollama` or `nvidia`.                            |
| `SYNAPSE_GRAPH_BACKEND`  | `falkordblite` | `falkordblite`, `falkordb` or `neo4j`.                             |
| `SYNAPSE_STRICT_PROMPTS` | `false`        | Raise instead of falling back when a prompt is unrecognised.       |

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The suite covers the resolution fixtures, the prompt manifest, the API response
contracts, an end-to-end ingest including temporal supersession, and both
directions of metadata/graph divergence.

## Known limitations

These are real, and worth stating plainly rather than discovering during a demo.

### There is no authentication by default

The console sends no credentials, so every endpoint is open. That is acceptable
bound to `127.0.0.1`, where only local processes can reach it, and is the reason
the default host is loopback rather than `0.0.0.0`.

If you expose the service — a tunnel, a port forward, a container with a
published port — set `SYNAPSE_AUTH_TOKEN`. Without it, startup prints a banner
naming the endpoints that become publicly writable, including snapshot
generation, which produces a downloadable dump of the entire graph.

There is no per-user identity and no role model. `access_level` on the query
simulator is a parameter the caller supplies, not an identity the server
verifies: it demonstrates what access control _would_ withhold, and is not
itself an access control.

### Resolution quality is heuristic, and the queue is the safety net

Entity resolution uses normalisation, legal-suffix stripping, a small nickname
table, token-set overlap and Jaro-Winkler. It has no world knowledge. It will
not know that two differently named products are the same one, and it can be
fooled by names that look similar but are not.

The design consequence: the resolver is deliberately conservative, and anything
it is unsure about — plus anything it left separate that the alias rules think
should be one entity — lands in the Curation review queue rather than being
merged silently. A missed merge is visible and fixable. A wrong merge corrupts
the graph and is not.

Confirming a merge in the queue records the decision; it does not rewrite the
graph. Graphiti owns node identity, and destructively merging nodes here would
desynchronise the provenance stored against the secondary node's episodes.

### Extraction quality reflects the stub, not the ontology

With the default stub, entities are proper nouns, code-style identifiers and
acronyms; relations come from a closed vocabulary of cues. It will miss facts
stated indirectly, and it will occasionally type an entity wrongly. That is a
property of the stub, not of the pipeline: point `SYNAPSE_LLM_PROVIDER` at a
real model and the same ontology and temporal logic apply to much better
extractions.

### Uploaded files are parsed, not sandboxed

`pypdf`, `python-docx` and BeautifulSoup parse whatever they are given, in this
process. Uploads are capped by size and extension, and text is normalised before
it reaches the graph, but a malicious file targeting a parser vulnerability is
not defended against. Do not point this at untrusted uploads without isolating
the parsing step.

There is also no virus scanning, no content sniffing beyond the extension, and
no limit on how much text a single document can contribute to the graph.

### The pre-ingest filter can drop real content

Boilerplate detection is conservative and rule-based (page markers, copyright
lines, confidentiality footers, exact-duplicate hashes), but it is still a
guess. Every drop is logged with the text and the rule that fired, the drop rate
is exposed as a metric, and an unusual rate raises a reviewable calibration
proposal — because this is the one place a false positive removes content before
it can ever reach the graph.

### Single-process, single-writer

The pipeline is an in-process asyncio worker. There is no queue, no retry
backoff and no horizontal scaling. Episode writes use an intent-then-commit
receipt so that a crash is detectable afterwards rather than silent, and
`/observability/consistency` reports the divergence — but recovery is manual.

The embedded graph is snapshotted to disk after each document and on shutdown.
A crash mid-document loses that document's episodes, which then surface as
`stuck_pending` receipts.
