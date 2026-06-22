# GPU Unit Economics

A small, defensible model for the numbers AI-infrastructure finance teams actually argue about: **cost per GPU-hour, cost per million tokens, gross margin, depreciation sensitivity, and reserved-vs-spot break-even.**

It is deliberately compact and readable — pure-Python, standard library only, every formula stated in code and pinned by a hand-computed test. The point is not a product; it is to make the unit economics of a GPU fleet legible and to show the assumptions behind each number explicitly.

> **All default figures are illustrative assumptions**, sourced from public GPU specs and typical data-center figures (see [Assumptions & sources](#assumptions--sources)). They are not vendor quotes. Swap in real numbers and the model re-prices everything.

## Quick start

```bash
# no install needed — stdlib only
PYTHONPATH=src python3 -m gpu_econ.cli

# or install the console script
pip install -e .
gpu-econ

# run the tests (42 hand-computed assertions)
PYTHONPATH=src python3 -m pytest
```

## What it computes

| # | Output | Module | The question it answers |
|---|---|---|---|
| 1 | Fully-loaded cost per GPU-hour | `cost_per_hour` | What does one GPU-hour actually cost (depreciation + power + opex)? |
| 2 | Cost per 1M tokens | `cost_per_token` | Which GPU generation serves inference cheapest per token? |
| 3 | Gross margin | `margin` | What margin does a usage-based price earn, by SKU? |
| 4 | Depreciation sensitivity | `depreciation` | How much does a 3- vs 6-year useful life swing unit cost and EBITDA? |
| 5 | Reserved-vs-spot break-even | `reserved_vs_spot` | Above what utilization does committing beat on-demand? |

## Sample output (default scenario)

```
1. Fully-loaded cost per GPU-hour (default assumptions)
GPU      depr/hr  power/hr   opex/hr  $/prov-hr  $/billable-hr
H100       $0.77     $0.07     $0.17      $1.01          $1.45
H200       $0.90     $0.07     $0.20      $1.17          $1.67
B200       $1.16     $0.10     $0.26      $1.52          $2.17

2. Cost per 1M tokens (inference, sorted cheapest first)
GPU      tok/hr (eff)  $/prov-hr  $/1M tokens
B200       15,120,000      $1.52        $0.10
H200        8,568,000      $1.17        $0.14
H100        6,300,000      $1.01        $0.16

3. Gross margin at on-demand list price (per billable hour)
GPU     price/hr   cost/hr  profit/hr   margin  annual GP/GPU
H100       $2.50     $1.45      $1.05   42.0%      $6,442.27
H200       $2.50     $1.67      $0.83   33.1%      $5,067.27
B200       $2.50     $2.17      $0.33   13.3%      $2,043.96

4. Depreciation useful-life sensitivity (H100, the 3-vs-6-year debate)
life (yr)   depr/hr  $/prov-hr  annual depr/GPU
        3     $1.03      $1.27        $9,000.00
        6     $0.51      $0.76        $4,500.00

  Fleet of 1,000 H100s: 6-year life reports $4,500,000.00 more annual EBITDA than 3-year life.

5. Reserved vs on-demand break-even (buyer view, 12-month term)
Break-even utilization: 64.0%
  Cheaper at default 70% utilization: reserved (saves $1,314.00)
```

## How the model works

All money is USD, power is kW, time is hours. The conventions are shared across every module so the numbers reconcile:

- **Straight-line depreciation** — `annual = capex * (1 - residual_value_frac) / useful_life_years`; per-hour = annual / 8,760.
- **Power cost per GPU-hour** — `power_kw * PUE * $/kWh`. PUE scales the GPU's board power up to total facility power (cooling, distribution), so overhead is not double-counted.
- **Non-power opex per GPU-hour** — `capex * opex_frac_of_capex_per_year / 8,760` (staff, maintenance, bandwidth, real estate).
- **Fully-loaded cost per provisioned hour** = depreciation + power + opex. This is the cost of *owning* the GPU for an hour, independent of whether it is busy.
- **Utilization** converts provisioned hours to *billable* hours. A GPU provisioned one hour at 70% utilization produces 0.7 hours of sellable work but still incurs the full provisioned cost — so cost per billable hour = provisioned cost / utilization.
- **Tokens** — effective tokens per hour = `tokens_per_sec * 3600 * utilization` (only utilized seconds produce tokens). Cost per 1M tokens = provisioned hourly cost / (effective tokens per hour / 1e6).
- **Reserved-vs-spot** — a reservation is paid for every hour of the term; on-demand only for hours used. Break-even utilization `u* = reserved_rate / on_demand_rate`; above it, reserving is cheaper.

Why two GPU "cost" bases (provisioned vs billable)? Because **revenue is earned on billable hours but cost is incurred on provisioned hours** — conflating them is the most common error in GPU-cloud unit economics, so the model keeps them distinct.

## The depreciation point (why output #4 matters)

GPU useful life is a real accounting lever with billions at stake. When Microsoft extended server/network equipment lives from 4 to 6 years (FY2023), it added ~$3.7B to operating income. The Burry-vs-hyperscaler debate is whether AI accelerators are durable infrastructure (long life, low annual depreciation, higher reported EBITDA) or fast-obsolescing inventory (short life, the opposite). Output #4 quantifies the swing for any fleet so the assumption is explicit rather than buried.

## Assumptions & sources

Defaults live in [`src/gpu_econ/inputs.py`](src/gpu_econ/inputs.py) as frozen dataclasses with validation. Illustrative values:

| GPU | capex | board power | tokens/sec | useful life | residual |
|---|---|---|---|---|---|
| H100 | $30,000 | 0.70 kW | 2,500 | 4 yr | 10% |
| H200 | $35,000 | 0.70 kW | 3,400 | 4 yr | 10% |
| B200 | $45,000 | 1.00 kW | 6,000 | 4 yr | 10% |

Data-center defaults: $0.08/kWh blended industrial power, PUE 1.3, opex 5% of capex/yr. Workload defaults: 70% utilization, $2.50/GPU-hr on-demand, $1.60 reserved, 12-month term. Capex and throughput figures are public-spec order-of-magnitude estimates, not quotes — they are the knobs you turn for a real engagement.

## Project layout

```
src/gpu_econ/
  inputs.py            # shared frozen-dataclass input contract (the assumptions)
  cost_per_hour.py     # 1. fully-loaded $/GPU-hour
  cost_per_token.py    # 2. $/1M tokens + cross-GPU comparison
  margin.py            # 3. gross margin + price sweep
  depreciation.py      # 4. useful-life sensitivity + EBITDA swing
  reserved_vs_spot.py  # 5. buyer break-even
  cli.py               # scenario runner (prints the report above)
tests/                 # 42 hand-computed assertions, one file per module
```

## License

MIT.
