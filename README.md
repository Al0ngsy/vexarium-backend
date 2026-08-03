# VEXARIUM Backend

Trading signal and options analysis tool — informational only, not financial advice.

## Setup

```bash
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env  # fill in your keys
pytest
uvicorn app.main:app --reload
```