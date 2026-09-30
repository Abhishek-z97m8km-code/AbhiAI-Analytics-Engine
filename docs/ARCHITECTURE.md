# Architecture Documentation

## Overview

AbhiAI Analytics Engine follows a layered architecture where each layer has a single responsibility and communicates through typed, validated contracts. The architecture enforces a critical principle: **the LLM is not the calculator** — all numerical reasoning happens in deterministic analytics engines.

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER QUESTION                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  DATA MANAGER                                    │
│  ┌─────────────────┐  ┌─────────────────────────────────────┐  │
│  │  DataLoader     │  │  Dataset Registry (multi-dataset)   │  │
│  │  CSV/XLSX       │  │  • Active dataset tracking          │  │
│  │  Size limits    │  │  • Backend preservation (legacy/duckdb)│ │
│  └─────────────────┘  └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                   ANALYTICS ENGINES                              │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐  │
│  │    Pandas Engine        │  │      DuckDB Engine          │  │
│  │  • In-memory            │  │  • Disk-backed (views)      │  │
│  │  • ≤1M rows             │  │  • No pandas materialization│  │
│  │  • Full pandas ops      │  │  • Bounded result sets      │  │
│  │  • CSV/XLSX/Parquet     │  │  • CSV/Parquet only         │  │
│  └─────────────────────────┘  └─────────────────────────────┘  │
│                              ↓                                   │
│              Parameterized Query Builder                         │
│              Identifier Validation                               │
│              Operation/Filters/GroupBy Allowlists                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              BUSINESS INSIGHT ENGINE (Phase 5A)                 │
│  • KPI overview (revenue, cost, profit, units, margin)          │
│  • Period comparisons (adjacent periods)                        │
│  • Trend classification (4+ periods, descriptive only)          │
│  • Dimension ranking (highest/lowest by metric)                 │
│  • Contribution analysis (% share of total)                     │
│  • Profitability relationships (revenue-cost-profit)            │
│  • MAD anomaly detection (|robust z| ≥ 3.5)                    │
│  • Limitations tracking (missing data, zero denom, causality)   │
│                                                                 │
│  Input: Pre-computed aggregates (kpis, groups, time_rows)       │
│  Output: InsightReport (typed insights + evidence + limitations)│
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              QUESTION/INTENT LAYER (Phase 5B)                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Deterministic Interpreter                               │   │
│  │  • Compositional cue matching (metric, dimension, op)   │   │
│  │  • Schema-aware validation                               │   │
│  │  • Ambiguity → clarification                             │   │
│  │  • Hinglish support (deterministic)                      │   │
│  │  • Safety: blocks injection, SQL, shell, API            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Optional Semantic Interpreter (local Ollama)            │   │
│  │  • Strict JSON schema                                    │   │
│  │  • Closed operation/metric/dimension validation          │   │
│  │  • Confidence threshold (≥0.7)                           │   │
│  │  • Cannot calculate — only classifies intent             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  Typed AnalyticsIntent → AnalyticsOperation handlers           │
│  • KPI_LOOKUP, RANKING, COMPARISON, TREND, CONTRIBUTION,       │
│    PROFITABILITY, ANOMALY, DIAGNOSTIC, SUMMARY                  │
│  Output: BusinessQuestionResult (facts + evidence + limitations)│
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              GROUNDED RESPONSE LAYER (Phase 5C)                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Deterministic Renderer                                  │   │
│  │  • Concise / Standard / Detailed modes                   │   │
│  │  • Currency-disciplined formatting (K/M/B/T, no $)       │   │
│  │  • Multi-language: English, Hindi, Hinglish              │   │
│  │  • Forecast/unsupported → helpful messages               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Optional Local LLM (Ollama)                             │   │
│  │  • Bounded context (12K chars)                           │   │
│  │  • Exact JSON output schema                              │   │
│  │  • Max 2 attempts, temperature 0                         │   │
│  │  • FaithfulnessValidator checks every attempt            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  FaithfulnessValidator                                   │   │
│  │  Rejects:                                                │   │
│  │  • Unsupported numbers (not in evidence)                 │   │
│  │  • Currency symbols when currency unknown                │   │
│  │  • Metrics not in source facts                           │   │
│  │  • Categories not in source facts                        │   │
│  │  • Periods not in source facts                           │   │
│  │  • External causal claims                                │   │
│  │  • Forecast language                                     │   │
│  │  Accepts: Safe paraphrases of grounded facts             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│  Output: BusinessResponse (answer + method + faithfulness)     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              STRUCTURED REPORT ENGINE (Phase 5D)                │
│  • Three report types: Business Overview, Sales Performance,    │
│    Financial Performance                                         │
│  • Three detail levels: Executive (5), Standard (10), Detailed  │
│    (20) findings                                                 │
│  • Sections: Executive Summary, KPI Overview, Revenue/Cost/     │
│    Profitability Analysis, Trend, Dimension Performance,        │
│    Contribution, Anomalies, Limitations                          │
│  • Chart specs: KPI cards, Line (time-series), Bar (dimension)  │
│  • Evidence traceability: Every element links to evidence IDs   │
│  • Validation: Orphan checks, size bounds, no raw rows          │
│  • Serialization: JSON round-trip with BusinessReport.from_dict │
│  Output: BusinessReport (portable, typed, validated)            │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Dataset Registration

```python
# Pandas backend (default)
manager.open("data.csv")
# → DataLoader reads CSV → DataFrame → DatasetRecord(frames, backend="legacy")

# DuckDB backend (explicit)
manager.open("large.csv", mode="duckdb")
# → DuckDBDatasetStore.register_csv() → view → DatasetRecord(frames={}, backend="duckdb")
```

### 2. Analytics Context Collection

```python
collect_analytics_context(manager, dataset_id, granularity="monthly")
```

**Pandas path:**
- Load DataFrame → discover_schema() → pandas groupby/resample → bounded aggregates

**DuckDB path:**
- DuckDBDatasetStore.sums() → full-dataset KPIs
- DuckDBDatasetStore.grouped_analysis() → dimension aggregates
- DuckDBDatasetStore.time_series_analysis() → period aggregates
- No raw rows returned — only aggregates

### 3. Insight Generation (Phase 5A)

```python
BusinessInsightEngine().analyze(
    dataset_id, backend, kpis, groups, time_rows, limitations, metadata
)
```

Deterministic conversion of aggregates → typed insights with evidence.

### 4. Question Answering (Phase 5B)

```python
BusinessQuestionEngine().ask(manager, question, dataset_id, semantic_service)
```

1. Collect analytics context (bounded aggregates)
2. Interpret question → AnalyticsIntent (deterministic or semantic)
3. Validate intent against schema
4. Execute handler for operation type
5. Return BusinessQuestionResult (facts + evidence)

### 5. Response Generation (Phase 5C)

```python
BusinessResponseEngine().respond(result, mode, language, service)
```

1. Deterministic render from facts
2. If service available and status=ANSWERED:
   - Serialize facts/evidence → LLM prompt
   - Get LLM response (JSON)
   - FaithfulnessValidator.validate()
   - If valid → use LLM response
   - If invalid → retry once → fallback to deterministic
3. Return BusinessResponse

### 6. Report Generation (Phase 5D)

```python
BusinessReportEngine().generate(manager, report_type, detail_level, language, response_service)
```

1. Collect analytics context + generate insights
2. Filter findings by detail-level bounds
3. Generate executive summary via response engine
4. Build sections, findings, charts with evidence IDs
5. Validate report structure
6. Return BusinessReport

## Safety Boundaries

| Boundary | Implementation |
|----------|----------------|
| No arbitrary SQL | ParameterizedQueryBuilder + allowlisted operations |
| No identifier injection | `validate_identifier()` with regex `^[a-zA-Z_][a-zA-Z0-9_]*$` |
| No eval/exec | No dynamic code execution anywhere |
| No LLM calculation | LLM only receives pre-computed facts |
| No raw row leakage | Bounded aggregates only (100 preview, 1K grouped, 10K time-series) |
| No external authority | LLM cannot access filesystem, shell, network |
| Currency discipline | Currency metadata required for symbols; otherwise currency-neutral |
| Causality discipline | Explicit "causality_not_established" limitation on every report |

## Type Contracts

All inter-layer communication uses frozen dataclasses with `to_dict()`/`from_dict()` for serialization:

- `DatasetInfo`, `DatasetLoadResult` — data loading
- `DatasetMetadata`, `AnalyticalRequest`, `AnalyticalResult` — analytics engines
- `BusinessSchema` — schema discovery
- `BusinessInsight`, `InsightReport` — Phase 5A
- `AnalyticsIntent`, `AnalyticsFact`, `BusinessQuestionResult` — Phase 5B
- `BusinessResponse`, `GenerationMethod`, `FaithfulnessStatus` — Phase 5C
- `BusinessReport`, `ReportKPI`, `ReportFinding`, `ReportChartSpec` — Phase 5D

## Backend Selection Logic

```python
def open(self, path, mode=None):
    selected_mode = selected_dataset_store(mode)  # env var or explicit
    if selected_mode == DUCKDB_STORE:
        return self.open_duckdb(path)
    return self.loader.open(path)  # pandas
```

- **Default**: Pandas (legacy) — preserves existing behavior
- **Explicit**: `mode="duckdb"` or `ABHIAI_DATA_STORE=duckdb`
- **No silent fallback**: DuckDB errors propagate; never falls back to pandas

## Testing Strategy

- **Primitive tests**: Schema discovery, comparisons, trends, MAD anomalies
- **Integration tests**: DataManager + InsightEngine (both backends)
- **Question tests**: All operation types, ambiguity, injection resistance
- **Response tests**: Rendering modes, languages, faithfulness validation
- **Report tests**: Types, detail levels, serialization, validation
- **Benchmark tests**: 1M-row registration, KPI accuracy, report timing

## Dependencies

| Package | Purpose | Required |
|---------|---------|----------|
| duckdb | Scalable analytics engine | Yes |
| pandas | Standard analytics engine, data loading | Yes |
| ollama | Optional local LLM | No (optional) |
| pytest | Testing | Dev only |
| ruff/mypy | Linting/type-checking | Dev only |