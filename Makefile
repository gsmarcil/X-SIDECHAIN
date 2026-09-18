.PHONY: test run check

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

check: test
	PYTHONPATH=src python3 -m compileall -q src tests

run:
	PYTHONPATH=src python3 -m x_sidechain

