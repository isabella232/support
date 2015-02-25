# SuPPort process notes

- Removing the lib path setup
- Cut out pp_cdb and things depending on it (config.py)

- can't import nanotime from faststat
- no ufork pypi package


## TODO

- remove all instances of:
  - protected
  - asf
  - mayfly
  - cal
  - topos
  - opscfg
- include contrib.net (+ other contrib things?)
- absolute imports (async -> support.async)

### ServerGroup

- split console into separate module?
- split out SimpleSupportApplication

- run became serve_forever and run had an `event` kwarg that was called after server.start() and we removed it
- removed dev kwarg
- start() may not be part of public API (needs cleanup/clarification)

### NetworkEndointModel (currently known as ServerModel)

- logical name (key)
- addresses/tiers
- connect/read retries/markdown, timeouts
- pooling options
- ssl options
- long-term todo: UDP (no connection, so connection_mgr is a funny concept)

## Deps

Based on Ubuntu, at least the following libs are required

- libssl-dev
- libffi-dev

## Design decisions worth highlighting

- Global scope mitigated with explicit context objects for every aspect of an Application
