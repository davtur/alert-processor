PYTHON ?= python3

.PHONY: test run

test:
	$(PYTHON) -m unittest discover -s tests -v

run:
	$(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
