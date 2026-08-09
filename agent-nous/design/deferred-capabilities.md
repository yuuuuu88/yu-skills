# Deferred capability boundaries

## Observability

`agent_nous.observability` provides a secret-free `TraceExporter` boundary, an in-memory test exporter, and a locked JSONL exporter. Engine trace records can be exported without adding an OpenTelemetry SDK dependency; `trace_id`, `span_id`, and `parent_span_id` remain compatible with W3C/OpenTelemetry correlation. The exporter allowlist excludes prompt, document, credential, and arbitrary output fields. OTLP export remains an adapter choice and is not enabled by default.

## MCP

`agent_nous.mcp` validates HTTPS authorization/resource endpoints, PKCE S256, OAuth `resource`, tool JSON Schema, and token-like tool arguments. `McpConnector` accepts an injected transport, never performs network I/O itself, and keeps the bearer token outside the JSON-RPC body. A host must provide a separately governed network adapter; this package does not claim that an arbitrary transport is contained.

## Resource budgets

`agent_nous.resources` enforces portable wall-time, output, tool-call, and concurrent-agent budgets. OS-level CPU/memory/network containment is intentionally not claimed: those require a host/provider adapter with actual cgroup/egress enforcement.

## Durable store topology

`agent_nous.durability.DurabilityProfile` makes the file-store guarantee explicit. `local` is the default. `shared_fs` documents and validates the precondition that all workers use one shared POSIX filesystem with compatible `flock` and atomic-rename semantics. This is not a distributed database; a future Postgres/SQLite-server adapter must implement the same TaskStore boundary before multi-host deployment is advertised.

## Runtime integration

`engine-run` accepts `resources`, `durability`, and `observability` from the same validated configuration file used for agent commands and loop limits. The engine applies the resource budget at the phase/agent boundary, exports only sanitized trace metadata when a JSONL path is configured, and validates the selected durability profile before starting. This is a portable runtime boundary: it does not silently imply OS CPU/memory/egress enforcement, live MCP networking, OTLP vendor delivery, or distributed database semantics.
