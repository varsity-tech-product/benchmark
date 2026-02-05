# Quant Tutor Benchmark Dataset Pipeline

A complete four-stage data pipeline for creating the Quant Tutor Evaluation Dataset - a benchmark for evaluating AI tutoring capabilities in financial education.

## Overview

This pipeline transforms raw financial Q&A data from multiple sources into a structured benchmark dataset with synthesized tutoring components:

- **Learner Profiles**: Inferred knowledge level, background, and learning goals
- **Tutoring Strategies**: Pedagogical approaches and teaching plans
- **Synthetic Responses**: AI-generated tutoring responses from multiple models

## Features

- Multi-source data ingestion (HuggingFace, GitHub, web scraping)
- Memory-efficient XML parsing for large datasets
- Async LLM synthesis with multiple model support
- Checkpointing for resumable processing
- Schema validation with Pydantic
- Automated dataset card generation
- Pre-commit hooks for code quality

## Data Sources

| Source | Type | Description |
|--------|------|-------------|
| FiQA | Research | Financial QA from forums (BeIR benchmark) |
| FinQA | Research | Numerical reasoning over financial reports |
| ConvFinQA | Research | Conversational financial QA |
| TAT-QA | Research | Tabular and textual QA |
| Money.SE | Community | Stack Exchange personal finance Q&A |
| SEC/CFPB/FINRA | Authoritative | Government financial education content |

## Installation

```bash
# Clone the repository
git clone https://github.com/varsity-tech-product/benchmark.git
cd benchmark

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install pre-commit hooks
pre-commit install
```

## Configuration

### API Key Setup

Create a `.env` file with your OpenRouter API key:

```bash
echo "OPENROUTER_API_KEY=your_key_here" > .env
```

Get an API key at [openrouter.ai](https://openrouter.ai/)

### Model Configuration

Edit `scripts/lib/llm_utils.py` to customize the models used for synthesis:

```python
MODELS = [
    "openai/gpt-4o-mini",
    "anthropic/claude-3-haiku",
    "google/gemini-2.5-flash",
    "x-ai/grok-4.1-fast",
]
```

## Usage

### Quick Start (Sample Data)

```bash
# Run full pipeline with 50 samples
source .venv/bin/activate

# Stage 1: Ingest research datasets
python scripts/01_ingest/ingest_research_datasets.py

# Stage 2: Structure the data
python scripts/02_structure/structure_research_datasets.py --sample 50

# Stage 3: Synthesize tutoring components
python scripts/03_synthesize/synthesize_tsr.py --sample 50

# Stage 4: Validate and package
python scripts/04_validate/validate_schema.py
python scripts/04_validate/create_dataset_card.py
```

### Full Pipeline

#### Stage 1: Ingestion

Download raw data from various sources:

```bash
# Research datasets (FiQA, FinQA, ConvFinQA, TAT-QA)
python scripts/01_ingest/ingest_research_datasets.py

# Stack Exchange (requires ~200MB download)
python scripts/01_ingest/ingest_stack_exchange.py

# Authoritative sources (SEC, CFPB, FINRA)
python scripts/01_ingest/ingest_authoritative_docs.py
```

#### Stage 2: Structuring

Parse and normalize data into unified format:

```bash
# Research datasets
python scripts/02_structure/structure_research_datasets.py

# Stack Exchange
python scripts/02_structure/structure_stack_exchange.py --min-score 5

# Authoritative docs
python scripts/02_structure/structure_authoritative_docs.py
```

#### Stage 3: Synthesis

Generate learner profiles, tutoring strategies, and responses:

```bash
# Full synthesis
python scripts/03_synthesize/synthesize_tsr.py

# With options
python scripts/03_synthesize/synthesize_tsr.py \
    --sample 100 \
    --max-concurrent 5 \
    --checkpoint-every 50
```

The synthesis is resumable - if interrupted, run the same command to continue.

#### Stage 4: Validation & Packaging

Validate schema and generate dataset card:

```bash
python scripts/04_validate/validate_schema.py
python scripts/04_validate/create_dataset_card.py
```

## Project Structure

```
benchmark/
├── configs/
│   └── prompts.yaml           # LLM prompt templates
├── data/
│   ├── 00_raw/                # Downloaded raw data
│   ├── 01_structured/         # Parsed JSONL files
│   ├── 02_synthesized/        # LLM-augmented data
│   └── 03_packaged/           # Final validated output
├── scripts/
│   ├── 01_ingest/             # Data download scripts
│   ├── 02_structure/          # Parsing scripts
│   ├── 03_synthesize/         # LLM synthesis pipeline
│   ├── 04_validate/           # Validation & dataset card
│   └── lib/                   # Shared utilities
│       ├── schemas.py         # Pydantic models
│       └── llm_utils.py       # LLM API utilities
├── tests/                     # Unit tests
├── notebooks/                 # EDA notebooks
├── .env.template              # Environment template
├── .pre-commit-config.yaml    # Pre-commit hooks
├── pyproject.toml             # Tool configuration
└── requirements.txt           # Dependencies
```

## Output Schema

Each record in the final dataset contains:

```json
{
  "id": "unique_record_id",
  "source_id": "original_source_id",
  "source_dataset": "fiqa|finqa|convfinqa|tatqa|...",
  "question": "original question text",
  "reference_answer": "original answer text",
  "tags": ["topic1", "topic2"],
  "context": "optional table/document context",
  "learner_profile": {
    "knowledge_level": "beginner|intermediate|advanced",
    "financial_background": "description",
    "learning_goals": ["goal1", "goal2"],
    "potential_misconceptions": ["misconception1"],
    "emotional_context": "curious|anxious|..."
  },
  "tutoring_strategy": {
    "approach": "Socratic|direct|scaffolded|...",
    "steps": ["step1", "step2", "step3"],
    "key_concepts": ["concept1", "concept2"],
    "analogies_or_examples": ["analogy1"],
    "follow_up_questions": ["question1"]
  },
  "synthetic_response": "AI-generated tutoring response",
  "teacher_model": "model_used_for_synthesis",
  "synthesis_timestamp": "ISO_timestamp"
}
```

## CLI Options

All scripts support common flags:

| Flag | Description |
|------|-------------|
| `--sample N` | Process only N records (for testing) |
| `--help` | Show all available options |

### Synthesis-specific options:

| Flag | Description |
|------|-------------|
| `--max-concurrent N` | Max parallel API calls (default: 5) |
| `--checkpoint-every N` | Save progress every N records (default: 50) |
| `--reset` | Clear checkpoint and start fresh |
| `--no-checkpoint` | Disable checkpointing |

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Pre-commit Hooks

The following checks run automatically on commit:

- Code formatting (black)
- Linting (ruff)
- Schema validation
- Unit tests
- Trailing whitespace, YAML/JSON validation

Run manually:

```bash
pre-commit run --all-files
```

### Adding New Data Sources

1. Create ingestion script in `scripts/01_ingest/`
2. Create structuring script in `scripts/02_structure/`
3. Output to `StructuredQA` schema (see `scripts/lib/schemas.py`)
4. Add tests in `tests/`

## Cost Estimation

LLM synthesis costs depend on record count and models used:

| Records | Est. API Calls | Est. Cost (GPT-4o-mini) |
|---------|----------------|-------------------------|
| 100 | 300 | ~$0.50 |
| 1,000 | 3,000 | ~$5 |
| 10,000 | 30,000 | ~$50 |

Using mixed models (including free tiers) can reduce costs significantly.

## License

This project is for research and educational purposes.

Dataset sources have varying licenses:
- Stack Exchange: CC BY-SA 4.0
- Research datasets: See original licenses
- Government sources: Public domain

## Citation

```bibtex
@dataset{quant_tutor_benchmark,
  title={Quant Tutor Benchmark Dataset},
  year={2024},
  description={Financial QA benchmark with synthesized tutoring components}
}
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes with tests
4. Run `pre-commit run --all-files`
5. Submit a pull request
