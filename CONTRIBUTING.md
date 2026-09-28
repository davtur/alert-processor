# Contributing

Issues and pull requests are welcome. Bug reports go in a GitHub issue. Vulnerability reports go through [private advisory reporting](https://github.com/davtur/alert-processor/security/advisories/new), not a public issue.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make test
```

Python 3.12 or newer. Tests are `unittest` and need no extra packages beyond `requirements.txt`.

## Pull requests

- Keep the change focused. A new action type, a new model provider, and a docs fix are three pull requests.
- Runtime dependencies stay in both `requirements.txt` and `pyproject.toml`.
- New behaviour needs a test that fails without the change. Cluster, SMTP, and xAI calls stay mocked or skipped; tests must not use a real kubeconfig.
- Do not add an executable action (shell, apply, delete of anything other than one Pod) without a matching guard test and a SECURITY.md update.
- Do not commit secrets, kubeconfigs, or a filled-in `.env`.
- Describe why the change exists in the pull request. `make test` should be green.

By submitting a contribution you agree it is licensed under the Apache License 2.0.
