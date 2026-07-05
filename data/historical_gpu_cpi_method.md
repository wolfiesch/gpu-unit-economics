# CPI normalization method

`usd_2026` in `historical_gpu_prices.csv` is pinned to the latest available 2026 CPI observation fetched during this dataset pass.

Source series:

- FRED series: `CPIAUCSL`
- Name: Consumer Price Index for All Urban Consumers: All Items in U.S. City Average
- Frequency: monthly
- Seasonal adjustment: seasonally adjusted
- Source URL: https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL
- Local snapshot: `data/cpi_aucsL_source.csv`

Pinned base:

```text
base_month=2026-05-01
base_cpi=333.979
```

Formula:

```text
usd_2026 = usd_nominal * 333.979 / CPIAUCSL[row_observation_month]
```

Rules:

- Row observation dates are mapped to their calendar month: `YYYY-MM-01`.
- Historical rows use the CPI value for their own observation month.
- Rows dated after `2026-05-01` use the pinned `2026-05-01` base CPI until a future explicit normalization update changes the base.
- `usd_nominal` remains the reported historical value or the midpoint of a reported range.
- `usd_2026` is general CPI-normalized dollars, not hardware-specific deflated cost.
- Reruns must use the pinned base above unless this method file is intentionally updated.
