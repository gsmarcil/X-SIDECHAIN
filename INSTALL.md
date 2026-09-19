# X-SIDECHAIN — Guía de instalación / Install guide

## 🇪🇸 Instalación rápida (3 comandos)

Si solo quieres que funcione:

```bash
git clone https://github.com/gsmarcil/X-SIDECHAIN.git
cd X-SIDECHAIN
./install.sh
```

El script verifica todo (Linux, Python ≥ 3.11, `venv`, `pip`), crea el entorno
virtual `.venv`, instala el paquete en modo editable, comprueba que el comando
`x-sidechain` existe, que los archivos web quedaron empaquetados, crea
`x-sidechain.json` a partir del ejemplo (sin sobrescribir el tuyo si ya
existe) y hace una prueba de humo. No pide `sudo` ni instala paquetes del
sistema por ti; si falta algo, te dice exactamente qué instalar.

Después de instalar, activa el entorno y lanza la UI:

```bash
. .venv/bin/activate
x-sidechain ui --no-browser --config x-sidechain.json
```

---

## Requirements

- **Linux** (the installer refuses to run anywhere else)
- **Python 3.11 or newer** (`python3 --version`)
- The `venv` module for your Python (Debian/Ubuntu: `python3.x-venv`)
- A working `pip` (the installer bootstraps it with `ensurepip` if needed)
- Internet access for the first install (to fetch `pip`/`setuptools`/`wheel` upgrades)

## Quick install

```bash
git clone https://github.com/gsmarcil/X-SIDECHAIN.git
cd X-SIDECHAIN
./install.sh
```

Options:

| Flag         | Effect                                            |
|--------------|---------------------------------------------------|
| `--recreate` | delete `.venv` and rebuild it from scratch        |
| `--no-smoke` | skip the final config-validation smoke test       |
| `--help`     | show usage                                        |

The installer is idempotent: re-running it is safe. It never overwrites an
existing `x-sidechain.json` and never touches files outside the repo.

Useful Make targets (see `Makefile`):

```bash
make install   # runs ./install.sh
make smoke     # validate-config + check web assets are packaged
make check     # unit tests + compileall
make ui        # launch the web UI (needs x-sidechain.json)
make deb       # build the Debian package into dist/
```

## Manual install (step by step)

If you'd rather do it by hand, `install.sh` does exactly this:

```bash
python3 --version            # must be >= 3.11
python3 -m venv .venv        # create the virtualenv
. .venv/bin/activate         # activate it (every new shell!)
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .   # editable install
.venv/bin/x-sidechain --help # sanity check: console script exists
python -c "from importlib.resources import files; p = files('x_sidechain')/'web'/'index.html'; assert p.is_file()"
cp -n x-sidechain.example.json x-sidechain.json   # only if you don't have one
chmod 600 x-sidechain.json
x-sidechain validate-config --config x-sidechain.example.json
```

Edit `x-sidechain.json` afterwards to add your providers and agents
(see `docs/CONFIGURATION.md`).

## Launching the UI

```bash
. .venv/bin/activate
x-sidechain ui --no-browser --config x-sidechain.json   # headless; prints the URL
```

Without `--no-browser` it tries to open your browser. Default port is 8765;
override with `--port 9000`. The web assets (`web/index.html`, `app.js`,
`live.js`, `styles.css`) are served from the installed package, so the UI
works as long as the package install succeeded — `make smoke` verifies this.

Started without `--config`, the interface loads
`~/.config/x-sidechain/config.json` when that file exists. This is what the
desktop entry in the Debian package uses, so a launcher needs no arguments.

## Installing system-wide instead

The repository also builds a Debian package, which puts the command on your
`PATH` without a virtualenv and adds a desktop entry, icons and a manual page:

```bash
make deb
sudo dpkg -i dist/x-sidechain_0.5.0_all.deb
```

It depends on `python3 (>= 3.11)` alone. Use this when you want the program
installed; use `./install.sh` when you want to work on it.

## Troubleshooting

**1. `python3` is older than 3.11** (e.g. `Python 3.10.x`)
The installer stops with `FAIL ... too old`. Install a newer Python
(e.g. `sudo apt install python3.11` on Debian/Ubuntu, `sudo dnf install
python3.11` on Fedora) and re-run `./install.sh`.

**2. `No module named venv` / `ensurepip is not available`**
On Debian/Ubuntu the `venv` module ships separately:
`sudo apt install python3.11-venv` (use the number matching your Python).
Then re-run `./install.sh`.

**3. `pip` upgrade fails or is stuck**
The installer retries 3 times, then fails with `pip upgrade failed 3 times`.
Check your network/proxy, then re-run. You can also skip network upgrades on a
subsequent manual run, but a modern `pip` avoids most install issues.

**4. `x-sidechain: command not found`**
You forgot to activate the virtualenv — this is the most common error.
The console script lives at `.venv/bin/x-sidechain` and is only on your
`PATH` after activation:

```bash
. .venv/bin/activate
x-sidechain --help
```

Activation is per-shell: you need it again in every new terminal. Alternatively
call `.venv/bin/x-sidechain` with the full path.

**5. Web assets missing (`web/index.html` not found)**
The `ui` command serves its HTML/JS/CSS from the installed package. The
installer asserts this explicitly. If it fails, rebuild cleanly:
`./install.sh --recreate`. The package data comes from the
`[tool.setuptools.package-data]` section in `pyproject.toml`.

**6. Permission errors / "don't use sudo"**
Never run the installer with `sudo`. Installing as root makes `.venv` and
`x-sidechain.json` root-owned, and everything breaks later as your normal
user. The installer refuses to run as root for this reason; if you are on a
machine that genuinely has no other user, set `X_SIDECHAIN_ALLOW_ROOT=1`.
If you already installed as root, fix ownership
(`sudo chown -R "$USER":"$USER" .`) or start over: `rm -rf .venv` and re-run
`./install.sh` as yourself. X-SIDECHAIN itself creates workspace dirs as
`0700` and files as `0600`, so a root-owned install also breaks the audit
trail.

**7. `pip install -e .` fails**
Read the actual pip error. Common causes: no network, or a `setuptools`
older than this project needs (the installer upgrades it in step [5/8], so
this usually only happens on manual installs). Try `./install.sh --recreate`.

**8. Broken `.venv` after an interrupted run**
Just re-run `./install.sh`; it's idempotent. If the venv itself is corrupt
(half-created), use `./install.sh --recreate` to wipe and rebuild it.
