# `docs/` index

Project-internal documentation. The top-level [README.md](../README.md) is the
user-facing intro (Home Assistant component first); these files go deeper for
anyone working on the code.

| File                                       | Purpose                                                    |
|--------------------------------------------|------------------------------------------------------------|
| [architecture.md](architecture.md)         | Package layout, module map, log severities, state machines |
| [protocol.md](protocol.md)                 | SHIP / SPINE / EEBUS on-wire quirks, frames, handshake     |
| [usage.md](usage.md)                       | CLI flags, env vars, examples                              |
| [mqtt.md](mqtt.md)                         | Home Assistant MQTT Discovery topology + debug knobs       |
| [troubleshooting.md](troubleshooting.md)   | Common failure modes and how to diagnose them              |

Adjacent reference:
- [`../README.md`](../README.md) — repository overview, Home Assistant install guide.
