VENV := .venv
PY   := $(VENV)/bin/python
BIN  := $(VENV)/bin/x-sidechain

.PHONY: test check run install smoke ui deb clean

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

check: test
	PYTHONPATH=src python3 -m compileall -q src tests

run:
	PYTHONPATH=src python3 -m x_sidechain validate-config --config x-sidechain.example.json

install:
	./install.sh

# What install.sh checks at the end, on its own: the command works and the
# interface can find the page it serves.
smoke:
	@test -x $(BIN) || { echo "not installed; run: make install" >&2; exit 1; }
	$(BIN) validate-config --config x-sidechain.example.json
	$(PY) -c "from importlib.resources import files; \
	web = files('x_sidechain') / 'web'; \
	missing = [n for n in ('index.html','app.js','live.js','styles.css','favicon.png') \
	           if not (web / n).is_file()]; \
	raise SystemExit('missing web assets: %s' % missing if missing else 0)"
	@echo "smoke: command and web assets are in place"

ui:
	@test -f x-sidechain.json || { echo "no x-sidechain.json; run: make install" >&2; exit 1; }
	$(BIN) ui --config x-sidechain.json

deb:
	./packaging/build-deb.sh

clean:
	rm -rf build dist *.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
