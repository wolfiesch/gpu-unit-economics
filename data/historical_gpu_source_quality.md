# Historical GPU Source Quality Notes

Use these rules when consolidating draft price rows into the historical GPU pricing CSV. The schema is fixed by `data/historical_gpu_schema.csv`; put extra context in `notes`.

## Confidence grades

- `A`: official vendor/OEM launch price, official cloud price, audited public dataset, or primary-source marketplace export with clear provenance, dates, fields, and license.
- `B`: archived retailer page, completed-sale evidence, reputable article, SEC/OEM bill-of-materials evidence, or archived benchmark/review table with explicit price, date, SKU, and condition.
- `C`: current listing, forum post, chart image, index point, marketplace snapshot without completed-sale proof, or secondary source that can corroborate another row but not stand alone.
- `D`: exclude from price rows. Use only in source notes or exclusion logs when the source is empty, inaccessible, unverifiable, too coarse, or outside the date/SKU scope.

Every consolidated price row must have `source_id`, `confidence`, and enough `notes` to explain why the source supports that row.

## Price normalization

- Preserve the reported amount in `nominal_price`. If the source reports a range, keep the range string and put the midpoint in `usd_nominal`.
- Convert non-USD prices using the exchange rate for the observation date or the source period midpoint. State the rate source and date in `notes`.
- Leave `usd_2026` blank until CPI normalization is implemented consistently across the full dataset.
- Use `sample_count` only for real observation counts. Leave it blank for single pages, articles, and inferred OEM estimates.
- Do not mix listing, sold, MSRP, and cloud hourly prices. Encode the evidence type in `price_type`, then explain caveats in `notes`.
- Treat cloud hourly prices as service prices, not hardware resale prices. They belong in `market_segment=cloud` and `price_type=cloud_hourly`.

## Date windows

- Use the observation date when a source provides one. Use the archive capture date only when the page content lacks a clearer date, and say so in `notes`.
- Use period midpoint dates for monthly, quarterly, or yearly aggregates. Put the original period in `period_label`.
- Current model SKUs start no earlier than their launch or first-availability date. Do not backfill H100, H200, B200, or later SKUs into pre-launch periods.
- Pre-LLM enterprise baselines should anchor to observed enterprise resale, OEM, cloud, or launch evidence. Do not infer 2018 trough prices for V100, P100, or T4 from consumer crypto-glut behavior.
- Consumer crypto-proxy rows should use consumer GPUs and consumer market evidence. Keep them separate from enterprise accelerators.

## Market segmentation

- `current_ai_sku`: modern AI accelerator or system evidence at or after product availability. Use this for H100, H200, B200, GB200, MI300, and similar SKUs.
- `enterprise_pre_llm`: enterprise accelerator baseline evidence before the LLM demand shock. Use this for V100, P100, T4, and similar datacenter cards when the source reflects enterprise, OEM, cloud, or resale markets.
- `consumer_crypto_proxy`: consumer GPU evidence used to represent crypto-cycle pricing pressure. Use this for GTX/RTX/Radeon consumer cards with retail or used-market evidence.
- Do not compare tracks as if they are the same market. Track differences must survive consolidation.

## Forbidden conflations

- Do not use United-Compute/gpu-price-tracker as historical evidence. Inspected JSON prices were empty for V100, A100 80GB, and RTX 3070.
- Do not use generic Kaggle references. A Kaggle source is candidate-only until a concrete dataset slug is named and its fields, coverage, provenance, and license are audited.
- Do not use PCPartPicker as a bulk historical source. It has no public API suitable for this dataset; use only manual spot checks with clear dates and screenshots/archives.
- Do not use eBay official sold search or Terapeak as 2018 historical evidence unless exportable, date-bounded completed-sale data for the exact SKU is available. Treat normal visible eBay sold-search windows as insufficient for 2018.
- Do not treat MSRP as a used-market floor or a resale observation.
- Do not treat active listings as completed sales.
- Do not treat consumer crypto-cycle declines as enterprise accelerator resale declines without direct enterprise evidence.
- Do not collapse system prices into card prices unless the source gives a defensible system-to-GPU allocation. Use `price_type=system_allocated_capex`, and explain the allocation in `notes`.

## Acceptance checks for the consolidated CSV

The orchestrator should run these checks after merging drafts:

1. Header equals exactly: `sku,vendor,track,market_segment,price_type,condition,date,period_label,currency,nominal_price,usd_nominal,usd_2026,source_id,confidence,sample_count,notes`.
2. Every row has non-empty `sku`, `vendor`, `track`, `market_segment`, `price_type`, `condition`, `date`, `period_label`, `currency`, `nominal_price`, `usd_nominal`, `source_id`, and `confidence`.
3. `confidence` is one of `A`, `B`, `C`, or `D`; production price rows should not include `D` except in a quarantine or rejected-row artifact.
4. `track` is one of `current_ai_sku`, `enterprise_pre_llm`, or `consumer_crypto_proxy`.
5. `market_segment` is one of `enterprise`, `consumer`, `cloud`, or `system`.
6. `price_type` is one of `launch_price`, `msrp`, `retail_list`, `used_sold`, `used_list`, `oem_estimate`, `system_allocated_capex`, `cloud_hourly`, or `index_point`.
7. `condition` is one of `new`, `used`, `refurbished`, `mixed`, `unknown`, or `not_applicable`.
8. `date` parses as ISO `YYYY-MM-DD`; aggregate periods use the documented midpoint or anchor date.
9. `source_id` is non-empty in every row, and each `source_id` is stable within the consolidated price CSV. If a separate source table is created, validate it against the source schema in the assignment, not against the price CSV schema.
10. Current AI SKUs do not appear before product launch or first-availability dates.
11. H100, H200, B200, GB200, MI300, and similar current AI SKUs are not assigned to pre-launch period labels.
12. V100, P100, and T4 rows are not labeled as crypto-glut trough evidence unless the source is direct enterprise resale evidence.
13. Consumer crypto-proxy rows do not use enterprise accelerator SKUs.
14. Enterprise pre-LLM rows do not use consumer GPU-only market evidence.
15. Active listings use `used_list` or `retail_list`; completed transactions use `used_sold`.
16. Rows whose `source_id` or `notes` name any `source_name` from `data/drafts/excluded_sources.csv` are absent from production price rows, except when the row uses that source only in a `C` confidence corroboration note allowed by the matching `allowed_use` value.
17. `usd_nominal` is numeric for single prices and numeric midpoint for ranges; range semantics remain visible in `nominal_price` and `notes`.
18. `usd_2026` is either blank for all rows or populated by one documented CPI method across all rows.
## Known data gaps

- Standalone B200 GPU/card capex is not sourced yet. The production dataset includes GB200 superchip and rack-scale Blackwell estimates from Tom's Hardware/HSBC, but those combine Grace CPU and multiple B200 GPUs or full rack systems. Do not treat them as standalone B200 capex. See `data/historical_gpu_gaps.csv`.

