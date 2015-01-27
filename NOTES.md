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


## Deps

Based on Ubuntu, at least the following libs are required

- libssl-dev
- libffi-dev
