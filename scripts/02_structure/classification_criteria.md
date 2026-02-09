# Classification Criteria

## Overview

The structured Q&A data (119,441 records) is classified into **7 categories based on data source**. Each source has distinct Q&A style, complexity level, and data format, making source-based classification a natural and deterministic split.

**Classification method:** Deterministic mapping from the `source_dataset` field — no keyword matching or heuristics involved. Every record is assigned to exactly one category with zero ambiguity.

**Script:** `scripts/02_structure/classify_data.py`

---

## Categories

### 1. `reddit` — Community Financial Q&A

| Attribute | Value |
|---|---|
| Records | 69,865 (58.5%) |
| Source datasets | reddit.personalfinance (32,772), reddit.financialindependence (11,501), reddit.investing (10,813), reddit.stocks (10,773), reddit.realestateinvesting (2,938), reddit.tax (1,068) |
| Original file | reddit_hq.jsonl |

**Data characteristics:**
- Real-world personal finance questions from Reddit users
- Informal, conversational language with personal context (age, income, life situation)
- Topics span the full spectrum: budgeting, investing, debt, tax, retirement, real estate
- Answers are community-sourced, often opinionated and experience-based
- Long question bodies (~1,000 chars avg) with moderate answers (~1,300 chars avg)

**Primary evaluation focus:**
- **Tone** (20% of LLM judge): Requires warm, empathetic, non-judgmental responses matching the informal register of real users sharing vulnerable financial situations
- **Engagement** (25% of LLM judge): Must sustain learner interest in diverse life scenarios — from a 19-year-old inheriting $100K to a 30-year-old with no savings habit
- **Pedagogy** (30% of LLM judge): Teaching financial concepts to non-experts who may have misconceptions or emotional biases

---

### 2. `fiqa` — Financial Industry Q&A

| Attribute | Value |
|---|---|
| Records | 17,072 (14.3%) |
| Source datasets | fiqa |
| Original file | research_datasets.jsonl |

**Data characteristics:**
- Open-ended financial Q&A from the FIQA research dataset
- Expert-level answers covering tax, business finance, investment, and personal finance
- No tabular context or conversation history — pure text Q&A
- Short questions (~57 chars avg) with detailed answers (~2,700 chars avg)
- Tagged as `financial-qa`

**Primary evaluation focus:**
- **Correctness** (30% of automated): Requires factually accurate, technically precise answers on specific financial rules (e.g., IRS deduction rules, LLC tax treatment)
- **Concept Coverage** (35% of automated): Must cover key financial terminology and concepts in the answer
- **Clarity** (25% of LLM judge): Expert knowledge must be explained clearly, as questions often come from non-experts seeking professional guidance

---

### 3. `authoritative_docs` — Official Regulatory Content

| Attribute | Value |
|---|---|
| Records | 139 (0.1%) |
| Source datasets | sec_investor_gov (23), finra (86), cfpb (30) |
| Original file | authoritative_docs.jsonl |

**Data characteristics:**
- Q&A derived from official publications of SEC (Investor.gov), FINRA, and CFPB
- Authoritative, formal language with regulatory precision
- Topics: investor protection, securities regulation, consumer rights, fraud prevention, investment product education
- Shorter questions (~100 chars avg) with structured, informative answers (~1,400 chars avg)
- Tagged with `regulatory` and `investor-education`

**Primary evaluation focus:**
- **Correctness** (30% of automated): Regulatory information must be factually accurate — errors in this domain carry real-world legal consequences
- **Concept Coverage** (35% of automated): Must accurately convey regulatory terminology (SEC, FINRA rules, FDCPA, fiduciary duty, etc.)
- **Pedagogy** (30% of LLM judge): Official regulatory concepts must be taught in an accessible way to general investors

---

### 4. `stack_exchange` — Expert Community Q&A

| Attribute | Value |
|---|---|
| Records | 5,123 (4.3%) |
| Source datasets | money.stackexchange |
| Original file | stack_exchange.jsonl |

**Data characteristics:**
- Q&A from Money Stack Exchange, a community of financial knowledge seekers and experts
- Well-structured questions with clear problem statements
- Answers are vote-ranked, tending toward high quality and thoroughness
- Moderate questions (~95 chars avg) with long, detailed answers (~2,300 chars avg)
- No tags in the original data; topics span personal finance, tax, investing, credit, insurance
- International perspective (US, Canada, UK questions)

**Primary evaluation focus:**
- **Clarity** (25% of LLM judge): Stack Exchange culture values well-organized, structured answers — the tutor must match this expectation
- **Correctness** (30% of automated): Community-vetted answers set a high bar for factual accuracy
- **Pedagogy** (30% of LLM judge): Questions often seek deep understanding (e.g., "What is a FICO score and how is it related to a credit report?") requiring thorough educational responses

---

### 5. `finqa` — Financial Report Numerical Reasoning

| Attribute | Value |
|---|---|
| Records | 8,281 (6.9%) |
| Source datasets | finqa |
| Original file | research_datasets.jsonl |

**Data characteristics:**
- Numerical reasoning questions over real corporate financial reports
- Each record includes a `context` field with a financial table + surrounding text from 10-K/10-Q filings
- Answers include explicit reasoning chains (e.g., `divide(9896, 23.6%)`)
- Requires multi-step arithmetic: addition, subtraction, multiplication, division, percentage calculation
- Tagged with `numerical-reasoning` and `financial-reports`

**Primary evaluation focus:**
- **Correctness** (30% of automated): Must arrive at the exact numerical answer — partial credit is not meaningful for calculations
- **Pedagogy** (30% of LLM judge): Must explain the reasoning process step-by-step, teaching the learner how to derive the answer from the financial report
- **Clarity** (25% of LLM judge): Must clearly connect table data to the calculation steps, making the derivation reproducible

---

### 6. `tatqa` — Table-Based Financial Q&A

| Attribute | Value |
|---|---|
| Records | 16,552 (13.9%) |
| Source datasets | tatqa |
| Original file | research_datasets.jsonl |

**Data characteristics:**
- Questions requiring comprehension of financial tables from real corporate reports
- Each record includes a `context` field with a markdown table + descriptive text
- Answer types vary: `span` (extract from text/table), `multi-span` (multiple extractions), `arithmetic` (calculation with derivation)
- Simpler questions than FinQA on average but requires precise table reading
- Tagged with `tabular-reasoning` and `financial-documents`

**Primary evaluation focus:**
- **Correctness** (30% of automated): Must accurately extract or compute values from the table — answer types are explicitly labeled (span, arithmetic, multi-span)
- **Clarity** (25% of LLM judge): Must teach the learner how to locate and interpret information in financial tables
- **Reference Similarity** (35% of automated): Answers are often short and precise, requiring high semantic alignment with the reference

---

### 7. `convfinqa` — Conversational Financial Reasoning

| Attribute | Value |
|---|---|
| Records | 2,409 (2.0%) |
| Source datasets | convfinqa |
| Original file | research_datasets.jsonl |

**Data characteristics:**
- Multi-turn conversational Q&A over financial reports
- Each record includes `conversation_history` with 2-6 prior Q&A turns building toward the final question
- Requires understanding prior conversational context to answer the current question correctly
- Progressive numerical reasoning: each turn builds on previous calculations
- Tagged with `conversational`, `numerical-reasoning`, and `financial-reports`

**Primary evaluation focus:**
- **Engagement** (25% of LLM judge): Must maintain coherence across a multi-turn conversation, building understanding progressively — the hallmark of effective tutoring
- **Correctness** (30% of automated): Chain of reasoning must be mathematically accurate across turns — errors compound in multi-step conversations
- **Pedagogy** (30% of LLM judge): Must demonstrate scaffolded teaching — each turn should build on what was established before, not repeat from scratch

---

## Evaluation Dimensions Reference

The evaluation system (`eval/`) scores responses on 7 dimensions:

**LLM Judge (60% of total score):**
| Dimension | Weight | Scale | Description |
|---|---|---|---|
| Pedagogy | 30% | 0-10 | Teaching quality, scaffolding, misconception handling |
| Clarity | 25% | 0-10 | Language appropriateness, organization, jargon handling |
| Tone | 20% | 0-10 | Warmth, professionalism, emotional context matching |
| Engagement | 25% | 0-10 | Learner continuation likelihood, curiosity stimulation |

**Automated Metrics (40% of total score):**
| Dimension | Weight | Scale | Description |
|---|---|---|---|
| Concept Coverage | 35% | 0-1 | Fraction of key concepts mentioned |
| Reference Similarity | 35% | 0-1 | Embedding similarity to reference answer |
| Correctness | 30% | 0-1 | Factual accuracy proxy |

---

## Category-Dimension Emphasis Matrix

Which evaluation dimensions each category most stresses:

| Category | Pedagogy | Clarity | Tone | Engagement | Concept Coverage | Ref. Similarity | Correctness |
|---|---|---|---|---|---|---|---|
| reddit | ★★★ | ★★ | ★★★ | ★★★ | ★★ | ★ | ★★ |
| fiqa | ★★ | ★★★ | ★★ | ★★ | ★★★ | ★★ | ★★★ |
| authoritative_docs | ★★★ | ★★ | ★★ | ★★ | ★★★ | ★★ | ★★★ |
| stack_exchange | ★★★ | ★★★ | ★★ | ★★ | ★★ | ★★ | ★★★ |
| finqa | ★★★ | ★★★ | ★ | ★ | ★★ | ★★ | ★★★ |
| tatqa | ★★ | ★★★ | ★ | ★ | ★★ | ★★★ | ★★★ |
| convfinqa | ★★★ | ★★ | ★ | ★★★ | ★★ | ★★ | ★★★ |

★ = baseline, ★★ = important, ★★★ = critical

---

## Data Balance Note

The categories are intentionally unbalanced, reflecting the natural composition of available financial Q&A sources. This does **not** affect ablation experiments because:

1. **Ablation uses paired testing** — each benchmark item is compared against itself (full scaffold vs. scaffold minus component), so category size does not bias the comparison
2. **Per-category analysis** is always available — filter by category and run separate statistical tests
3. **Sampling is supported** — the synthesis pipeline (`synthesize_tsr.py --sample N`) can cap records per category if equal-sized subsets are needed for specific experiments
