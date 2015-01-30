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

### ServerGroup

- is .run() really a public part of the ServerGroup API?
- no_client_auth_req keyword arg => require_client_auth?
- dev kw arg?
- split console into separate module?
- split out SimpleSupportApplication

- run became serve_forever and run had an event kwarg called post-start and we removed it

## Deps

Based on Ubuntu, at least the following libs are required

- libssl-dev
- libffi-dev

## Design decisions worth highlighting

- Global scope mitigated with explicit context objects for every aspect of an Application
