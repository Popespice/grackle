# Architecture overview

grackle is a local-first live code visualizer for Python.

```mermaid
graph LR
    subgraph Browser["Browser (localhost:5173)"]
        UI["React UI\n(Sigma.js graph)"]
        WsClient["WS client\n(Zustand store)"]
    end

    subgraph Agent["Agent (127.0.0.1:7878)"]
        WsServer["WebSocket server\n(websockets 16)"]
        Registry["Adapter registry"]
        StaticParser["Static parser\n(ast — phase 2)"]
        RuntimeTracer["Runtime tracer\n(sys.monitoring — phase 6)"]
    end

    subgraph Types["@grackle/shared-types"]
        Schema["JSON Schema\n(source of truth)"]
        TS["TS types\n(codegen)"]
        Py["Python TypedDicts\n(codegen)"]
    end

    WsClient <-->|"WS envelopes\n{id, type, payload}"| WsServer
    WsServer --> Registry
    Registry --> StaticParser
    Registry --> RuntimeTracer
    Schema --> TS
    Schema --> Py
```

## Key seams

**Transport** — JSON WebSocket envelopes. `type` is an open string; unknown
types are ignored by both sides. Schema is the source of truth (ADR-0002).

**Adapter pattern** — `StaticParserAdapter` and `RuntimeAdapter` are structural
Protocols. The registry maps language strings to implementations. New languages
plug in without touching existing code (ADR-0003, phase 1).

**Type sharing** — JSON Schema → TS + Python via codegen. Parity verified in CI
and on every schema-touching pre-commit.

## Current state (phase 0)

`grackle serve` → WebSocket server on `127.0.0.1:7878` that replies to
`ping` envelopes with `pong`. The React frontend connects automatically and
shows a live `ConnectionBadge`. Static parser, runtime tracer, and graph
rendering arrive in phases 2–7.
