# Changelog

Notable changes to X-SIDECHAIN. Versions follow [Semantic Versioning](https://semver.org):
the major number changes when a configuration file or an audit log written by an
older version stops being readable, the minor number when a capability is added,
and the patch number for fixes alone. The version lives in
`src/x_sidechain/__init__.py`; the package metadata reads it from there.

## 0.5.1

### Fixed
- The session deadline was not enforced when a correction restarted the cycle.
  `DeadlineReached` existed but nothing raised it, so a session could start a
  fresh cycle past a wall-clock budget the documentation said it would not.
  The restart is now refused and `cycle.deadline_refused` recorded, mirroring
  how an exhausted call budget already refuses one.
- A provider could take an API key to a host of its choosing. urllib follows
  301, 302 and 303 by re-sending the request headers to whatever `Location`
  names; a provider replying `302` received the full `Authorization` value at
  another address, and the call still returned success. Redirects are now
  refused outright, with an error that names the fix.
- Only six header names were treated as carrying credentials, so a
  configuration naming its own header leaked that value into the error log. The
  test is inverted: headers known to carry nothing secret are listed, and every
  other outgoing value is redacted from provider text.
- `verify` crashed on a corrupt audit log instead of reporting one. A line that
  parsed as any JSON value other than an object raised `TypeError` or
  `AttributeError`; the command now returns a verdict for every malformed line,
  which is the question it exists to answer.
- The interface showed a finished session as having spent its whole budget: the
  end-of-run event derived the maximum from the calls actually made, so every
  completed session read `N / N`. It now carries the run's own final snapshot.
- The page carried `v0.4.0` while the package was 0.5.0.

### Changed
- The interface reads its version from `/api/config`, so the page cannot
  disagree with the engine serving it.
- Room events are appended under the lock that reads them, rather than relying
  on list appends being atomic.
- Python 3.12 and 3.13 are declared, matching what CI already tests.

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
