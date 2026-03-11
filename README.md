# Analytics Pipeline — Setup & Test Guide

LLM-driven analytics pipeline for a single-table SQL dataset. This guide walks through environment setup, data preparation, and running the full test suite.

---

## Prerequisites

- **Python 3.13+**
- **Conda** (Miniconda or Anaconda)
- **Kaggle account** (for data download)

---

## 1. Conda Environment Setup

### Option A: environment.yml (recommended)

```bash
conda env create -f environment.yml
conda activate gap
```

### Option B: Manual create

```bash
conda create -n gap python=3.13 -y
conda activate gap
```

---

## 2. Install Dependencies

```bash
# Core dependencies
make install

# Dev tools (mypy, ruff, pytest) — for typecheck, lint, tests
make install-dev
```

Or manually:

```bash
pip install -r requirements.txt
pip install mypy ruff pytest
```

---

## 3. Data Setup

The dataset (~160MB) is not in the repo. Download and convert it:

### 3.1 Download CSV

1. Go to [Kaggle - Gaming and Mental Health](https://www.kaggle.com/datasets/sharmajicoder/gaming-and-mental-health?select=gaming_mental_health_10M_40features.csv)
2. Download `gaming_mental_health_10M_40features.csv`
3. Place it in `data/`:

```bash
mkdir -p data
# Move your download to data/gaming_mental_health_10M_40features.csv
```

### 3.2 Convert to SQLite

```bash
make data
```

Or:

```bash
python3 scripts/gaming_csv_to_db.py
```

This creates `data/gaming_mental_health.sqlite` with the `gaming_mental_health` table.

### 3.3 Verify Data

```bash
python3 -c "
from pathlib import Path
import sqlite3
db = Path('data/gaming_mental_health.sqlite')
if db.exists():
    conn = sqlite3.connect(db)
    cur = conn.execute('SELECT COUNT(*) FROM gaming_mental_health')
    print(f'Rows: {cur.fetchone()[0]}')
    cur = conn.execute('PRAGMA table_info(gaming_mental_health)')
    print(f'Columns: {len(cur.fetchall())}')
    conn.close()
else:
    print('DB not found — run make data after downloading CSV')
"
```

---

## 4. OpenRouter API Key (for LLM tests)

Public tests and the benchmark call the OpenRouter API. Set your key:

```bash
# Option A: Export
export OPENROUTER_API_KEY=sk-or-...

# Option B: .env file (recommended)
cp .env.example .env
# Edit .env and set OPENROUTER_API_KEY
```

Get a key at [openrouter.ai](https://openrouter.ai/).

---

## 5. Run Tests

### 5.1 Unit & Integration Tests (no API)

Runs validation, LLM client, cache, token counting, result validation, edge cases, conversation tests:

```bash
make test
```

### 5.2 Public Integration Tests (requires API key)

Uses the real LLM for SQL generation and answer synthesis:

```bash
make test-public
```

> **Note:** `test-public` loads `.env` via `init_env()`. Ensure `OPENROUTER_API_KEY` is set in `.env` or your shell.

### 5.3 Full Check (typecheck + lint + test)

```bash
make check
```

---

## 6. Benchmark

```bash
make benchmark
```

Or with custom runs:

```bash
python3 scripts/benchmark.py --runs 5
```

Output includes avg/p50/p95 latency and success rate.

---

## 7. Example: Run a Single Query

```bash
conda activate gap
python3 -c "
from src import init_env
init_env()
from src.pipeline import AnalyticsPipeline
from scripts.gaming_csv_to_db import DEFAULT_DB_PATH

p = AnalyticsPipeline(db_path=DEFAULT_DB_PATH)
result = p.run('What are the top 5 age groups by average addiction level?')
print('Status:', result.status)
print('SQL:', result.sql[:100] + '...' if result.sql and len(result.sql) > 100 else result.sql)
print('Answer:', result.answer[:200] + '...' if len(result.answer) > 200 else result.answer)
"
```

---

## 8. Makefile Reference

| Target        | Description                                      |
|---------------|--------------------------------------------------|
| `make help`   | Show all targets (default)                       |
| `make install`| Install core dependencies                        |
| `make install-dev` | Install dev tools (mypy, ruff, pytest)       |
| `make data`   | Convert CSV → SQLite                             |
| `make test`   | Run tests (excl. public/LLM)                     |
| `make test-public` | Run public integration tests (needs API key) |
| `make benchmark` | Run benchmark (3 runs)                        |
| `make typecheck` | Run mypy on `src/`                            |
| `make lint`   | Run ruff check                                   |
| `make format` | Run ruff format                                  |
| `make check`  | typecheck + lint + test                          |

---

## 9. Quick Start (Full Flow)

```bash
# 1. Setup
conda env create -f environment.yml
conda activate gap

# 2. Data (download CSV to data/, then)
make data

# 3. API key (for test-public and benchmark)
cp .env.example .env
# Edit .env: set OPENROUTER_API_KEY

# 4. Verify
make check
make test-public
make benchmark
```

---

## 10. Optional: Langfuse Observability

For tracing and metrics, set in `.env`:

```
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
```

Tracing is a no-op when these are unset.

---

## Original Assignment

See `README.orig.md` for the full assignment description, tasks, and deliverables.
