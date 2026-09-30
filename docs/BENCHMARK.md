# Benchmark Documentation

## 1M-Row Benchmark Results

This document records measured benchmark results from the development environment. These are **not universal performance claims** — they represent observed timings on specific hardware with a specific synthetic dataset.

### Hardware & Software Context

| Component | Specification |
|-----------|---------------|
| **CPU** | Apple Silicon (M-series) |
| **RAM** | 16 GB |
| **OS** | macOS (Darwin) |
| **Python** | 3.10+ |
| **DuckDB** | 1.2.1 |
| **Pandas** | 3.0.5 |
| **DuckDB Config** | `memory_limit=4GB`, `threads=4`, `preserve_insertion_order=false` |

### Dataset Construction

| Property | Value |
|----------|-------|
| **Source** | Synthetic repetition of 50,000-row sales dataset |
| **Row Count** | 1,000,000 rows × 14 columns |
| **File Size** | 119 MB (CSV) |
| **SHA-256** | `f791d678687bfafd8adc7485403de3563da06173e41ebe2b0b0de4bf54c68f90` |
| **Columns** | Order Date, Region, Item Type, Sales Channel, Total Revenue, Total Cost, Total Profit, Units Sold, ... |
| **Date Range** | 2010-01-01 to 2017-07-29 |
| **Regions** | 7 unique |
| **Item Types** | 12 unique |
| **Sales Channels** | 2 (Online, Offline) |

> **Note**: This is a synthetic dataset created by repeating a 50K-row pattern 20×. It does not represent naturally diverse million-row data. Distribution characteristics (cardinality, skew, null rates) match the 50K source.

### Measured Timings

#### DuckDB Registration (CSV → View)

| Operation | Duration |
|-----------|----------|
| CSV registration + schema inference | **~6.895 seconds** |
| Peak memory during registration | ~2.35 MB (DuckDB streams; no pandas frame) |

#### Structured Report Generation (Phase 5D)

| Report Type | Detail Level | Duration |
|-------------|--------------|----------|
| Business Overview | Executive | ~0.723 s |
| Business Overview | Standard | ~0.724 s |
| Business Overview | Detailed | ~0.725 s |
| Sales Performance | Standard | ~0.725 s |
| Financial Performance | Standard | ~0.725 s |

**Breakdown (Standard Business Overview):**
- Analytics context gathering: ~0.35 s
- Insight generation: ~0.05 s
- Finding prioritization: ~0.01 s
- Section building: ~0.02 s
- Chart spec generation: ~0.01 s
- Response rendering: ~0.001 s
- **Total: ~0.724 s**

#### Business Questions (Phase 5B/5C)

| Question Type | Backend | Duration |
|---------------|---------|----------|
| KPI Lookup | DuckDB | ~0.72–0.76 s |
| Ranking | DuckDB | ~0.72–0.76 s |
| Comparison | DuckDB | ~0.72–0.76 s |
| Trend | DuckDB | ~0.72–0.76 s |
| Contribution | DuckDB | ~0.72–0.76 s |
| Diagnostic | DuckDB | ~0.72–0.76 s |
| Anomaly | DuckDB | ~0.72–0.76 s |

All questions return **297 aggregate rows** and **0 raw rows** — DuckDB stays active with no pandas frame materialization.

#### 50K Reference Dataset (Pandas Backend)

| Operation | Duration |
|-----------|----------|
| CSV load + profiling | ~0.30 s |
| Insight generation | ~0.17 s |
| Report generation | ~0.05–0.13 s |
| Question answering | ~0.18 s |

### Verified KPI Totals (1M Rows)

| Metric | Value |
|--------|-------|
| **Total Revenue** | 1,323,716,137,624.376 |
| **Total Cost** | 933,157,398,659.4012 |
| **Total Profit** | 390,558,738,964.9949 |
| **Total Units Sold** | 4,999,618,980 |
| **Profit Margin** | 29.5047199217% |

These values match an independent Decimal-based streaming calculation within floating-point tolerance.

### Aggregate Row Bounds

| Operation | Bound | Actual (1M dataset) |
|-----------|-------|---------------------|
| Preview rows | ≤100 | 100 |
| Grouped analysis (per dimension) | ≤1,000 | 7–12 rows |
| Time-series (per granularity) | ≤10,000 | 8–91 rows |
| Total aggregate rows per question | — | 297 |

### Limitations & Caveats

1. **Synthetic dataset**: The 1M-row benchmark uses a repeated 50K pattern. Real-world datasets with higher cardinality, more columns, wider date ranges, or different distributions will show different performance.

2. **Hardware dependent**: Timings measured on Apple Silicon with 16GB RAM. Performance will vary on x86, different RAM amounts, disk types (SSD vs HDD), and CPU core counts.

3. **DuckDB configuration**: Results use `memory_limit=4GB`, `threads=4`. Different settings will yield different timings.

4. **Cold vs warm**: Registration is a one-time cost. Subsequent queries on the same registered dataset are ~100-200ms (full scan).

5. **No pandas fallback**: The benchmark uses explicit DuckDB mode. The pandas backend cannot handle 1M rows (enforced 1M row limit).

6. **Single-file CSV**: Performance assumes a single CSV file. Multiple files, partitioned data, or Parquet format will differ.

7. **No concurrent load**: Measurements are single-threaded, single-user. Concurrent access not tested.

### Comparison: Pandas vs DuckDB (50K Rows)

| Metric | Pandas (Legacy) | DuckDB |
|--------|-----------------|--------|
| Load time | 0.30 s | 6.90 s |
| Memory peak | ~18 MB | ~2.35 MB |
| KPI query | N/A (error) | ~0.18 s |
| Grouped analysis | N/A (error) | ~0.18 s |
| Time analysis | N/A (error) | ~0.18 s |
| Report generation | 0.05–0.13 s | 0.72–0.73 s |

**Key insight**: DuckDB registration is slower but uses constant low memory. For datasets >100K rows, DuckDB's streaming aggregation outperforms pandas' in-memory approach. For small datasets, pandas is faster to load.

### Reproducing the Benchmark

To reproduce similar benchmarks:

```python
# Generate synthetic 1M-row dataset (not included in repo)
python -c "
import pandas as pd
df = pd.read_csv('examples/sample_sales.csv')
# Repeat 20x to get ~1M rows (adjust as needed)
pd.concat([df]*20, ignore_index=True).to_csv('benchmark_1m.csv', index=False)
"

# Run with DuckDB backend
from abhiai_analytics import DataManager
import time

manager = DataManager()
start = time.perf_counter()
result = manager.open('benchmark_1m.csv', mode='duckdb')
reg_time = time.perf_counter() - start
print(f'Registration: {reg_time:.3f}s')

start = time.perf_counter()
report = manager.generate_business_report()
report_time = time.perf_counter() - start
print(f'Report: {report_time:.3f}s')
```

### Historical Context

These benchmarks were originally measured during **Phase 4B.2 Milestone 5** and **Phase 5D** development of the private AbhiAI project (September 2026). They are preserved here as documented evidence of the analytics engine's capability to process million-row datasets with bounded memory and sub-second report generation.

---

**Disclaimer**: These benchmark results are specific to the development environment, dataset, and software versions documented above. They should not be interpreted as guaranteed performance for other environments, datasets, or workloads.