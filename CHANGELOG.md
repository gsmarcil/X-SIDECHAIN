# Changelog

Notable changes to X-SIDECHAIN. Versions follow [Semantic Versioning](https://semver.org):
the major number changes when a configuration file or an audit log written by an
older version stops being readable, the minor number when a capability is added,
and the patch number for fixes alone. The version lives in
`src/x_sidechain/__init__.py`; the package metadata reads it from there.

## 0.5.0

### Added
- `x-sidechain ui`: a local interface on the loopback address, behind a one-off
  token, streaming every public event of a running session and able to start
  one, steer it, and close its input.
- ChatGPT account authentication delegated to the official Codex CLI, with a
  `codex_cli` provider that never handles the credential itself.
- Session team and chair selection from the interface.
- `--version`.
- Debian package, desktop entry, manual page, and icons from 16 to 256 pixels.
- Apache-2.0 license.

### Changed
- `x-sidechain ui` with no `--config` now loads `~/.config/x-sidechain/config.json`
  when that file exists, so a desktop launch reaches a working session.
- The worst-case cycle cost, `3N + min(Q, N) + 2`, is one function shared by the
  engine, the configuration reader, and the interface.

### Fixed
- Provider API keys are no longer inherited by the Codex subprocess; its
  environment is an explicit allowlist.
- The account status check no longer runs on every interface request; it is
  cached and refreshed in the background.
- A viewer connecting after a session ended is told the run is over instead of
  waiting on a stream that will never speak.
- The reported call budget is the one a run is actually bounded by rather than
  the unset raw value.

## 0.4.0

### Added
- Quorum, abstentions, bounded restarts, and a session deadline.
- Transport retries and a response size cap.

### Fixed
- Owner-only audit logs and workspaces, and audit chain resume.
- Cleartext HTTP rejected for remote providers.
- Chair triage tolerant of prose-wrapped JSON.
- Provider error text contained per vendor and scrubbed of credentials.
