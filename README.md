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

# Optional — only needed for specific agent adapters:
# MISTRAL_API_KEY=your_mistral_key_here          # --agent mistral
# AWS_ACCESS_KEY_ID=your_aws_access_key_here      # --agent strands (Bedrock)
# AWS_SECRET_ACCESS_KEY=your_aws_secret_key_here   # --agent strands (Bedrock)
# AWS_DEFAULT_REGION=us-east-1                     # --agent strands (Bedrock)
# OPENAI_API_KEY=your_openai_key_here              # --agent openai / microsoft
# ANTHROPIC_API_KEY=your_anthropic_key_here        # --agent anthropic / strands
# GOOGLE_API_KEY=your_google_key_here              # --agent google
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

## Evaluation System (`bench/`)

The `bench/` directory contains the QuantTutorBench evaluation framework -- a two-axis benchmark that evaluates agents on both **quantitative finance expertise** (70%) and **tutoring effectiveness** (30%).

See `design_2026_2_12_updated.md` for the full design specification.

### Architecture

The evaluation system uses a three-LLM architecture:

1. **Student Simulator** -- DeepEval `ConversationSimulator` generates realistic multi-turn student interactions using persona profiles
2. **Agent Under Test (AUT)** -- The LLM agent being evaluated, accessed via pluggable adapter (7 supported: generic, openai, anthropic, google, mistral, strands, microsoft)
3. **Judge LLM** -- DeepEval `ConversationalGEval` scores the agent's tutoring quality across 7 dimensions

The evaluation follows a 5-phase per-task lifecycle: RESET → INTERACT → CAPTURE → EVALUATE → TEARDOWN.

### Two-Layer Structure

- **Layer 1** (~2000 single-turn Q&A): Tests quant domain knowledge via `LLMTestCase` + `GEval`
- **Layer 2** (~500 multi-turn tutoring): Tests tutoring ability via `ConversationalTestCase` + `ConversationalGEval` with 7D rubric

### Quick Start

```bash
# Validate the benchmark setup
cd bench
python run_benchmark.py test-e2e

# Run full benchmark with default model
python run_benchmark.py run --model deepseek/deepseek-chat-v3-0324

# Run with OpenAI Agents SDK adapter and custom judge model
python run_benchmark.py run --agent openai --model gpt-4o \
    --eval-model openai/gpt-4o --simulator-model openai/gpt-4o

# Run a single task for debugging
python run_benchmark.py run-single --task S01_ma_crossover \
    --persona beginner_no_finance --agent generic

# Run Layer 1 single-turn evaluation
python run_benchmark.py run-layer1 --max-items 50 \
    --agent-model deepseek/deepseek-chat-v3-0324

# List available tasks
python run_benchmark.py list-tasks

# Validate task schemas
python run_benchmark.py validate-tasks
```

### CLI Options

#### `run` -- Full benchmark

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `deepseek/deepseek-chat-v3-0324` | LLM model for agent under test (OpenRouter format) |
| `--agent` | `generic` | Agent adapter: `generic`, `openai`, `anthropic`, `google`, `mistral`, `strands`, `microsoft` |
| `--eval-model` | *(DeepEval default)* | LLM model for GEval judge |
| `--simulator-model` | *(default)* | LLM model for student simulator |
| `--tasks` | *(all)* | Comma-separated task IDs to run |
| `--personas` | *(all)* | Comma-separated persona IDs |
| `--trials` | `1` | Number of trials per task (for pass@k) |
| `--max-turns` | `5` | Max conversation turns per task |
| `--docker` | off | Use Docker sandbox for code execution |

#### `run-single` -- Single task debugging

| Flag | Default | Description |
|------|---------|-------------|
| `--task` | *(required)* | Task ID to run |
| `--persona` | `beginner_no_finance` | Persona ID |
| `--model` | `deepseek/deepseek-chat-v3-0324` | LLM model for agent under test |
| `--agent` | `generic` | Agent adapter: `generic`, `openai`, `anthropic`, `google`, `mistral`, `strands`, `microsoft` |
| `--eval-model` | *(DeepEval default)* | Judge model |
| `--simulator-model` | *(default)* | Simulator model |
| `--max-turns` | `5` | Max turns |

#### `run-layer1` -- Layer 1 single-turn evaluation

| Flag | Default | Description |
|------|---------|-------------|
| `--max-items` | *(all)* | Max items to evaluate |
| `--agent-model` | `deepseek/deepseek-chat-v3-0324` | LLM model for agent |
| `--eval-model` | *(DeepEval default)* | LLM model for GEval judge |
| `--no-deepeval` | off | Disable DeepEval, use keyword-matching fallback |

### Scoring System

**Task Score** = 0.70 x Quant Score + 0.30 x Tutor Score

- **Quant Score** = 0.50 x Result + 0.50 x Process
  - Result: correctness of final answer (via eval scripts)
  - Process: tool usage precision/recall + DeepEval process metrics
- **Tutor Score** = mean of 7 dimensions (D1-D7), each judged on 1-10 scale with 3x shuffled runs

### 7D Tutoring Rubric

| Dimension | Description |
|-----------|-------------|
| D1 - Level Detection | Correctly identifies and adapts to the student's knowledge level |
| D2 - Language Adaptation | Adjusts vocabulary and complexity to match the student |
| D3 - Scaffolding Calibration | Provides appropriate hints/steps without over-helping |
| D4 - Domain Accuracy | Financial/quant content is factually correct |
| D5 - Code Teaching | Code examples are correct, explained, and pedagogically sound |
| D6 - Empathetic Response | Handles confusion, mistakes, and frustration supportively |
| D7 - Safety Boundaries | Refuses harmful advice, adds appropriate disclaimers |

### Benchmark KPIs

| KPI | Description |
|-----|-------------|
| **OAS** | Overall Agent Score (weighted mean of all task scores) |
| **QAI** | Quant Agent Index (mean quant score across tasks) |
| **TEI** | Tutoring Effectiveness Index (mean tutor score across tasks) |
| **AS** | Adaptiveness Score (how well the agent adapts across personas) |
| **TMS** | Tool Mastery Score (precision x recall of tool usage) |
| **pass@k** | Fraction of tasks passing threshold in k trials |

### DeepEval Integration

The system uses 14 DeepEval components (v3.8.4+):

| Component | Layer | Purpose |
|-----------|-------|---------|
| `GEval` | L1 | Single-turn quant knowledge scoring |
| `ConversationalGEval` | L2 | 7D tutoring rubric evaluation |
| `ConversationSimulator` | L2 | Student persona simulation |
| `ConversationalGolden` | L2 | Task + persona configuration |
| `LLMTestCase` | L1/L2 | Single-turn test container |
| `ConversationalTestCase` | L2 | Multi-turn test container |
| `ToolCorrectnessMetric` | L2 | Tool call correctness |
| `ArgumentCorrectnessMetric` | L2 | Tool argument quality |
| `MCPUseMetric` | L2 | Single-turn MCP tool usage |
| `MultiTurnMCPUseMetric` | L2 | Multi-turn MCP tool usage |
| `StepEfficiencyMetric` | L2 | Tool call efficiency |
| `RoleAdherenceMetric` | L2 | Chatbot role consistency |
| `KnowledgeRetentionMetric` | L2 | Cross-turn knowledge retention |
| `TopicAdherenceMetric` | L2 | Topic focus maintenance |

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
├── bench/                     # Evaluation framework
│   ├── run_benchmark.py       # CLI entry point
│   ├── orchestrator/          # Core benchmark orchestration
│   │   ├── orchestrator.py    # 5-phase lifecycle orchestrator
│   │   ├── schemas.py         # Pydantic models (QuantTutorTask, TaskResult, etc.)
│   │   ├── simulator_config.py # ConversationSimulator configuration
│   │   ├── trace_assembler.py # Assembles DeepEval test cases from traces
│   │   ├── container_manager.py # Docker sandbox management
│   │   └── agent_adapters/    # Agent adapter plugins
│   │       ├── base_adapter.py       # Abstract base adapter
│   │       ├── registry.py          # Pluggable adapter registry
│   │       ├── generic_adapter.py   # OpenAI-compatible API adapter
│   │       ├── openai_adapter.py    # OpenAI Agents SDK adapter
│   │       ├── anthropic_adapter.py # Claude Agent SDK adapter
│   │       ├── google_adapter.py    # Google GenAI SDK adapter
│   │       ├── mistral_adapter.py   # Mistral AI SDK adapter
│   │       ├── strands_adapter.py   # AWS Strands Agents adapter
│   │       └── microsoft_adapter.py # Microsoft Agent Framework adapter
│   ├── evaluation/            # Evaluation metrics and scoring
│   │   ├── scoring.py         # Task scoring, benchmark KPIs (OAS/QAI/TEI/AS/TMS)
│   │   ├── test_scripts/      # Per-task evaluation scripts
│   │   └── deepeval_metrics/  # DeepEval metric wrappers
│   │       ├── quant_geval.py       # Layer 1 GEval for quant knowledge
│   │       ├── tutor_conv_geval.py  # 7D ConversationalGEval with 3x shuffled judge
│   │       ├── mcp_metrics.py       # ToolCorrectness + precision/recall
│   │       └── process_metrics.py   # 7 additional DeepEval process metrics
│   ├── layer1/                # Layer 1 single-turn evaluation
│   │   ├── data_loader.py     # Load synthesized Q&A items
│   │   └── runner.py          # Layer 1 batch runner
│   ├── mcp_servers/           # MCP tool servers
│   │   ├── core/tools.py      # Core quant tools (fetch_data, run_code, etc.)
│   │   ├── distractors/       # Distractor tools for tool selection testing
│   │   ├── proxy/mcp_proxy.py # Transparent tool call logging proxy
│   │   └── registry.py        # Tool registry
│   ├── tasks/                 # Task definition JSONs
│   ├── personas/              # Student persona JSONs
│   ├── student_code/          # Buggy code samples for debugging tasks
│   └── docs/reference/        # Reference materials for context
├── scripts/                   # Data pipeline scripts
│   ├── 01_ingest/             # Data download scripts
│   ├── 02_structure/          # Parsing, classification & quality inspection
│   ├── 03_synthesize/         # LLM synthesis pipeline
│   ├── 04_validate/           # Validation & dataset card
│   ├── 05_upload/             # HuggingFace upload
│   ├── estimate_cost.py       # OpenRouter cost estimation tool
│   └── lib/                   # Shared utilities
├── eval/                      # Legacy evaluation notes
├── tests/                     # Unit tests
├── notebooks/                 # EDA notebooks
├── design_2026_2_12_updated.md # Full design specification
├── .env.template              # Environment template
├── .pre-commit-config.yaml    # Pre-commit hooks
├── pyproject.toml             # Tool configuration
└── requirements.txt           # Dependencies
```

## CLI Quick Reference

All scripts support `--help` for full option listing.

### Data pipeline options:

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

### Benchmark evaluation commands:

| Command | Description |
|---------|-------------|
| `python bench/run_benchmark.py run` | Run full benchmark evaluation |
| `python bench/run_benchmark.py run-single --task <ID>` | Run single task for debugging |
| `python bench/run_benchmark.py run-layer1` | Run Layer 1 single-turn evaluation |
| `python bench/run_benchmark.py list-tasks` | List available tasks |
| `python bench/run_benchmark.py validate-tasks` | Validate task JSON schemas |
| `python bench/run_benchmark.py test-e2e` | Run end-to-end validation checks |

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
