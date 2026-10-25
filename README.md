# outward

Batch service that writes pending BankWorks *outward* integration files to a
mounted share for BizTalk, optionally archiving them and running successor
steps, then marks each task complete in the database.

See [REVIEW.md](REVIEW.md) for the review findings and the design of this version.

## Layout

```
app.py                 entry point / composition root (only module using okq8common)
config.yaml            settings, each overridable via environment variable
outward/
  config.py            typed, validated Settings
  models.py            domain value objects
  errors.py            exception hierarchy with exit codes
  repository.py        BankWorks stored procedures (oracledb + pandas live here)
  storage.py           mount checks, LOB streaming, atomic publish
  successors.py        hshl / hcsv / hbin successor strategies
  processor.py         processing of a single task
  service.py           batch run and exit-code policy
tests/                 pytest suite (no database needed)
```

## Running

```
pip install -r requirements.txt
python app.py
```

Exit codes: `0` ok, `70` unexpected error, `72` mount check failed,
`74` database/IO failure, `78` invalid configuration.

## Testing

```
pip install -r requirements-dev.txt
pytest --cov=outward
```
