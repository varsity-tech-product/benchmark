# Quant Tutor Benchmark Dataset Pipeline

A step-by-step pipeline for creating the Quant Tutor Evaluation Dataset -- a benchmark for evaluating AI tutoring capabilities in financial education.

## Prerequisites

- Python 3.10+
- An [OpenRouter](https://openrouter.ai/) API key (for synthesis step)
- A [HuggingFace](https://huggingface.co/settings/tokens) token (for upload step)

---

## Step 1: Set Up the Environment

```bash
git clone https://github.com/varsity-tech-product/benchmark.git
cd benchmark
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pre-commit install
```

**Verify:**

```bash
python -c "import pydantic, openai, zstandard, huggingface_hub; print('OK')"
pre-commit run --all-files
```

---

## Step 2: Configure API Keys

```bash
cp .env.template .env
```

Edit `.env` and fill in your keys:

```
OPENROUTER_API_KEY=your_key_here
HF_TOKEN=your_huggingface_token_here
```

**Verify:**

```bash
python -c "from dotenv import load_dotenv; import os; load_dotenv(); assert os.getenv('OPENROUTER_API_KEY') != 'your_key_here', 'Set your API key'; print('OK')"
```

---

## Step 3: Download Raw Data

Each ingest script downloads data into `data/00_raw/`.

```bash
# Research datasets (FiQA, FinQA, ConvFinQA, TAT-QA)
python scripts/01_ingest/ingest_research_datasets.py

# Stack Exchange Money Q&A (~200 MB download)
python scripts/01_ingest/ingest_stack_exchange.py

# Reddit finance subreddits (Arctic Shift API)
python scripts/01_ingest/ingest_reddit.py                  # all 6 subreddits
python scripts/01_ingest/ingest_reddit.py --subreddit tax   # single subreddit

# Authoritative sources (SEC, CFPB, FINRA)
python scripts/01_ingest/ingest_authoritative_docs.py
```

**Verify:**

```bash
ls data/00_raw/
# Expected: fiqa/  finqa/  convfinqa/  tatqa/  money.stackexchange.com/  reddit/  authoritative/
```

---

## Step 4: Structure the Data

Parsing scripts read from `data/00_raw/` and write JSONL to `data/01_structured/`.

```bash
# Research datasets
python scripts/02_structure/structure_research_datasets.py

# Stack Exchange
python scripts/02_structure/structure_stack_exchange.py --min-score 5

# Reddit
python scripts/02_structure/structure_reddit.py                          # all subreddits
python scripts/02_structure/structure_reddit.py --subreddit investing     # single subreddit
python scripts/02_structure/structure_reddit.py --min-post-score 20      # stricter filter
python scripts/02_structure/structure_reddit.py --sample 100             # quick test run

# Authoritative docs
python scripts/02_structure/structure_authoritative_docs.py
```

**Verify:**

```bash
wc -l data/01_structured/*.jsonl
# Should list record counts for each source
```

---

## Step 4.5: Classify Data

Classify structured records into 7 categories (reddit, fiqa, authoritative_docs, stack_exchange, finqa, tatqa, convfinqa) based on `source_dataset`. Output goes to `data/01_structured/classified/`.

```bash
python scripts/02_structure/classify_data.py
```

**Verify:**

```bash
ls data/01_structured/classified/
# Expected: reddit.jsonl  fiqa.jsonl  authoritative_docs.jsonl  stack_exchange.jsonl  finqa.jsonl  tatqa.jsonl  convfinqa.jsonl
```

Optionally, inspect data quality before synthesis:

```bash
# Inspect all structured datasets
python scripts/02_structure/inspect_quality.py

# Inspect a specific file
python scripts/02_structure/inspect_quality.py --file data/01_structured/stack_exchange.jsonl

# Reddit-specific quality inspection
python scripts/02_structure/inspect_reddit_quality.py
```

---

## Step 5: Synthesize Tutoring Data

This step calls LLM APIs to generate learner profiles, tutoring strategies, and synthetic responses. **This costs money** -- use the cost estimation tool below.

The synthesis reads from `data/01_structured/classified/` and writes per-dataset output files to `data/02_synthesized/`. It randomly selects from 10 diverse models per API call.

```bash
# List available datasets
python scripts/03_synthesize/synthesize_tsr.py --list

# Quick test (50 records per dataset)
python scripts/03_synthesize/synthesize_tsr.py --all --sample 50

# Synthesize all datasets
python scripts/03_synthesize/synthesize_tsr.py --all

# Synthesize specific dataset(s)
python scripts/03_synthesize/synthesize_tsr.py --dataset stack_exchange
python scripts/03_synthesize/synthesize_tsr.py --dataset finqa tatqa

# With options
python scripts/03_synthesize/synthesize_tsr.py --all \
    --max-concurrent 5 \
    --checkpoint-every 50
```

The synthesis is **resumable** -- if interrupted, re-run the same command to continue from the last checkpoint.

### Cost Estimation

```bash
# Check current OpenRouter balance
python scripts/estimate_cost.py balance

# Run sample and estimate full cost
python scripts/estimate_cost.py estimate --sample 100 --total 119441

# Monitor balance in real-time during synthesis
python scripts/estimate_cost.py monitor --interval 60
```

**Verify:**

```bash
ls data/02_synthesized/*.jsonl
```

---

## Step 6: Validate and Package

```bash
python scripts/04_validate/validate_schema.py
python scripts/04_validate/create_dataset_card.py
```

**Verify:**

```bash
ls data/03_packaged/
# Expected: quant_tutor_benchmark.jsonl  DATASET_CARD.md
```

---

## Step 7: Upload to HuggingFace

```bash
python scripts/05_upload/upload_to_huggingface.py \
    --repo-id your-org/quant-tutor-benchmark

# Or make it public
python scripts/05_upload/upload_to_huggingface.py \
    --repo-id your-org/quant-tutor-benchmark \
    --public
```

**Verify:** Open `https://huggingface.co/datasets/your-org/quant-tutor-benchmark` in your browser.

---

## Project Structure

```
benchmark/
├── configs/
│   └── prompts.yaml           # LLM prompt templates
├── data/
│   ├── 00_raw/                # Downloaded raw data
│   ├── 01_structured/         # Parsed JSONL files
│   │   └── classified/        # Category-split JSONL files
│   ├── 02_synthesized/        # LLM-augmented data
│   └── 03_packaged/           # Final validated output
├── scripts/
│   ├── 01_ingest/             # Data download scripts
│   ├── 02_structure/          # Parsing, classification & quality inspection
│   │   ├── structure_*.py     # Parsing scripts
│   │   ├── classify_data.py   # Data classification into 7 categories
│   │   ├── inspect_quality.py # General quality inspection
│   │   └── inspect_reddit_quality.py
│   ├── 03_synthesize/         # LLM synthesis pipeline
│   ├── 04_validate/           # Validation & dataset card
│   ├── 05_upload/             # HuggingFace upload
│   ├── estimate_cost.py       # OpenRouter cost estimation tool
│   └── lib/                   # Shared utilities
│       ├── schemas.py         # Pydantic models
│       └── llm_utils.py       # LLM API utilities
├── eval/                      # Evaluation system (separate requirements)
├── tests/                     # Unit tests
├── notebooks/                 # EDA notebooks
├── .env.template              # Environment template
├── .pre-commit-config.yaml    # Pre-commit hooks
├── pyproject.toml             # Tool configuration
└── requirements.txt           # Dependencies
```

## CLI Quick Reference

All scripts support `--help` for full option listing.

| Flag | Description |
|------|-------------|
| `--sample N` | Process only N records per dataset (for testing) |
| `--help` | Show all available options |

### Synthesis-specific options:

| Flag | Description |
|------|-------------|
| `--all` | Synthesize all classified datasets |
| `--dataset NAME [NAME ...]` | Synthesize specific dataset(s) by name |
| `--list` | List available datasets and exit |
| `--max-concurrent N` | Max parallel API calls (default: 5) |
| `--checkpoint-every N` | Save progress every N records (default: 50) |
| `--input-dir PATH` | Input directory with classified JSONL files |
| `--output-dir PATH` | Output directory for synthesized data |
| `--reset` | Clear checkpoint and start fresh |
| `--no-checkpoint` | Disable checkpointing |

## Data Sources

| Source | Type | Description |
|--------|------|-------------|
| FiQA | Research | Financial QA from forums (BeIR benchmark) |
| FinQA | Research | Numerical reasoning over financial reports |
| ConvFinQA | Research | Conversational financial QA |
| TAT-QA | Research | Tabular and textual QA |
| Money.SE | Community | Stack Exchange personal finance Q&A |
| Reddit | Community | Finance subreddits (r/personalfinance, r/investing, etc.) |
| SEC/CFPB/FINRA | Authoritative | Government financial education content |

## Troubleshooting

**`ModuleNotFoundError`**: Make sure the virtual environment is activated (`source .venv/bin/activate`) and dependencies are installed (`pip install -r requirements.txt`).

**Reddit download slow or fails**: The script uses the Arctic Shift API and is resumable -- if interrupted, re-run and it will pick up where it left off. Large subreddits (e.g. r/personalfinance) may take a while due to API pagination.

**Synthesis interrupted**: The pipeline is checkpoint-based. Re-run the same `synthesize_tsr.py` command and it will continue from the last saved checkpoint.

**HuggingFace upload auth error**: Ensure `HF_TOKEN` is set in `.env` and has write permissions. Generate a token at https://huggingface.co/settings/tokens with "Write" access.

**Pre-commit hooks fail**: Run `pre-commit run --all-files` to see which check failed, then fix and re-commit.

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Pre-commit Hooks

The following checks run automatically on commit: code formatting (black), linting (ruff), schema validation, unit tests, trailing whitespace, YAML/JSON validation.

```bash
pre-commit run --all-files
```

## License

This project is for research and educational purposes.

Dataset sources have varying licenses:
- Stack Exchange: CC BY-SA 4.0
- Reddit: Check subreddit and Reddit API terms
- Research datasets: See original licenses
- Government sources: Public domain

## Citation

```bibtex
@dataset{quant_tutor_benchmark,
  title={Quant Tutor Benchmark Dataset},
  year={2025},
  description={Financial QA benchmark with synthesized tutoring components}
}
```
