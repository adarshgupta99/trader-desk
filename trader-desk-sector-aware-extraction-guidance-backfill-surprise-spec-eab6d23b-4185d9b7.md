# Trader Desk: Sector-Aware Earnings Extraction + Guidance Tab + Historical Backfill

Paste-ready spec for Claude Code. Claude has not seen this before, so it is complete and self-contained. Sections 1 to 9 are the build; section 10 collects the owner decisions. Items marked **[OWNER DECISION]** are Adarsh's calls: do not decide them silently, build the default shown and make it a one-line config change.

---

## 0. What this is and why

Trader Desk is a Streamlit app for NSE F&O earnings tracking (~213 names), design language "Swiss Editorial / Charcoal Warm".

Today the transcript pipeline pulls a fixed set of 10 columns per stock per quarter (tone, guidance, evasiveness, etc.). Three things are being added:

1. **Sector-aware metrics.** An IT company is judged on TCV and attrition, a bank on NIM and GNPA, an FMCG company on volumes. The extraction now fills a block of sector-specific slots chosen from the company's sector, on top of the universal columns.
2. **A Guidance tab** per stock, showing formal guidance, soft outlook, sector slots, prior-quarter values, and a promised-vs-delivered check.

3. **Estimate-vs-actual SURPRISE** (section 5A): uploaded estimate snapshots vs structured NSE actuals, shown in the per-stock results view.

Then **backfill**: run the same extraction over past quarters so the dataset exists before anything is analysed on it. Adarsh's eventual aim is to regress these features against the actual post-results price move. **No model is built in this spec.** The only thing built now for that future is timestamp discipline in storage (section 5), because retrofitting it later is painful and getting it wrong makes any future model leak.

Domain source for the sector metrics: Adarsh's 13-Oct-2026 "How to Read Indian Earnings" cheat sheet (universal 8-check frame + per-sector cards). The metric lists in section 3 come straight from it.

---

## 1. Hard rules (carry-over, do not relax)

These are settled. Do not re-open them.

- **NSE-only transcript source.** Transcripts come from NSE corporate filings only. Never QuarterMark, AlphaStreet, company IR sites, or news copies as a source for extracted values. If the NSE original cannot be fetched, the value is `NOT_VERIFIED`, not filled from a copy.
- **Originals over copies.** Always the original NSE PDF, hashed.
- **LLM-only extraction.** No regex or keyword layers anywhere in extraction. The LLM reads the text and returns structured output. (Regex is fine for non-extraction plumbing such as parsing a URL or a date in a filename.)
- **Schema lock on the transcript sheet.** The sheet stays exactly as specified, same names, same order: `QUARTER | PRICE MOVE | MGMT CONFIDENCE | OVERALL TONE | Q&A TONE | GUIDANCE | ANALYST TONE | EVASIVE QS | QA DIVERGENCE | SOURCE` (10 columns - Adarsh removed the LM lexicon columns on 15 Oct 2026). Adarsh explicitly approved extending with sector-aware fields, so the new fields go in **their own separate block/sheet/table**, joined by (symbol, quarter). Never add columns into this sheet.
- **No invention.** A value either has verbatim transcript evidence (quote + location) or it is `NOT_GIVEN` / `NOT_VERIFIED`. Never infer a number the speaker did not say.
- **Secrets hygiene.** Never commit keys or tokens; `.env` stays gitignored. Store the populated dataset outside the repo (local SQLite/parquet) and commit code only. Hosting the app to show people is expected.
- **Analyst opinion articles stay out of any sentiment gauge.** Nothing here changes that.

---

## 2. Extraction mechanics (the proven v3 spec, unchanged)

This was validated on TCS over 6 quarters. Build it exactly; the sector block in section 3 is an addition to stage 2, not a change to the mechanics.

### 2.1 Input preparation
- Read the NSE PDF with **PyMuPDF**. Strip the cover page (disclaimer/cover boilerplate) before anything else.
- Store the SHA-256 of the original PDF bytes and of the cleaned text. Store the exact code version used, so any row can be re-run and compared ("hashes + rerun code").

### 2.2 Two-stage LLM extraction
- **Stage 1, structural segmentation (LLM).** The LLM splits the transcript into: management prepared remarks vs Q&A, and within Q&A into **numbered exchanges** (analyst question + management answer, each with a stable number, speaker names, and character offsets). Output is JSON. No regex splitting.
- **Stage 2, extraction over the numbered exchanges (LLM).** Every judgement cites exchange numbers. Outputs:
  - **OVERALL TONE, Q&A TONE, ANALYST TONE, MGMT CONFIDENCE:** integer scale **-2 to +2**, each with the exchange numbers that justify it.
  - **QA DIVERGENCE:** graded **N / MILD / STRONG** (none / mild / strong), with exchange cites. Divergence means management's Q&A answer contradicts or walks back what the prepared remarks implied. A contradiction found must be kept as evidence (the TCS Q2 FY26 demand-commitment contradiction is the reference case that stays MILD with its evidence quoted).
  - **EVASIVE QS:** count of questions answered evasively, under a **strict rubric**: evasive = the question's specific ask (number, date, yes/no) is not answered and the answer redirects or generalises. A question that is answered, even briefly or with "we do not guide on that" stated plainly, is **not** evasive. (v2 wrongly flagged a book-to-bill answer that had actually been answered; the strict rubric fixes this. Output the list of evasive exchange numbers, not just the count.)
  - **GUIDANCE:** one cell holding **formal status + soft outlook** (see section 4). Kept strictly separate inside the cell.
- **PRICE MOVE:** next-session close vs results-day close, as a percent. Computed from price data, not by the LLM. Stored with both dates.
- **SOURCE:** the NSE PDF URL, plus the two hashes.
- LLM output is validated against a JSON schema; on schema failure, retry once, then mark the row `EXTRACTION_FAILED` with the reason. Never write a partially valid row silently.

### 2.3 Cites format
Every cite is `{exchange_no | "prepared"}` plus a verbatim quote of up to ~40 words and character offsets into the cleaned text. A downstream check (plain code, not regex extraction) verifies each quote is an exact substring of the cleaned text; a quote that is not a substring fails the row.

---

## 3. Sector-aware schema

### 3.1 Structure
Three layers, joined on `(symbol, quarter)`:

1. **Universal 10 columns.** Unchanged (section 1).
2. **Universal frame block** (new sheet/table `earnings_frame`): the cheat sheet's 8 checks, as far as a transcript can answer them. See 3.2.
3. **Sector block** (new sheet/table `earnings_sector_slots`): the slots in 3.3 for that company's sector.

### 3.2 Universal frame fields (every company)
The cheat sheet's 8 checks. The transcript can only speak to some of them, so each field is filled **only from what management says on the call**; anything else stays `NOT_GIVEN`. Do not pull consensus or actuals from outside sources into this block (those live in the estimates/XBRL features, which are separate).

| field | from check | what to capture |
|---|---|---|
| `mgmt_margin_commentary` | 2 Margins | what management says about margin direction and drivers (quote + cite) |
| `guidance_formal` | 3 Guidance | see section 4 |
| `guidance_soft` | 3 Guidance | see section 4 |
| `one_offs_disclosed` | 4 One-offs | list of exceptional items, tax quirks, other-income items management names, each with amount if stated (quote + cite) |
| `cash_flow_commentary` | 6 Cash vs profit | working capital / operating cash flow remarks if any |
| `riders_announced` | 7 Riders | dividend, buyback, fundraise, acquisition, management change mentioned on the call |
| `sector_slots_ref` | 5 Sector quality | pointer to the sector block row |

Checks 1 (vs consensus) and 8 (what was priced in) are **not** transcript questions. They stay in the estimates/price features (surprise is specified in section 5A). Do not try to extract them here.

### 3.3 Sector map and slots
Each slot has: `key`, value (as spoken, plus a parsed number and unit where the speaker gave one), `status`, `evidence` (quote + cite), `period_basis` (QoQ / YoY / FY / LTM, as stated). Every slot supports four statuses, never a blank:

- `GIVEN`: a value stated by management, with evidence.
- `NOT_GIVEN`: the call has no statement on this. First-class state.
- `NOT_VERIFIED`: the NSE original could not be obtained, so nothing can be asserted.
- `NOT_APPLICABLE`: slot does not apply to this company (e.g. a sub-segment the company does not have).

The slot lists below are taken from the cheat sheet's "Watch first" bullets. A slot name that begins `ADD?` is **not** in the cheat sheet and is an **[OWNER DECISION]** (see 3.5).

**IT services** (TCS, Infosys, HCLTech, Wipro, Tech Mahindra and similar)
- `cc_revenue_growth_qoq`, `cc_revenue_growth_yoy` (constant currency, the real demand signal)
- `tcv_deal_wins` (total contract value, with currency)
- `book_to_bill`
- `ebit_margin` and `ebit_margin_vs_guided_band` (value stated, and the company's own band if any, and management's reason for any drift: wage hikes, pricing, acquisitions)
- `attrition_ltm`
- `net_headcount_adds`
- `fy_guidance_band` (Infosys and HCLTech guide the full year; a shift in the band outweighs the quarter)
- ADD? `ai_revenue_disclosed` (the cheat sheet notes TCS disclosed $3.1bn annualised AI revenue and calls out the trap of AI headlines substituting for organic demand)

**Private banks** (HDFC Bank, ICICI, Axis, Kotak, RBL, Federal and similar)
- `nii`, `nim` and `nim_direction` (direction matters more than level)
- `gnpa`, `nnpa`, `slippages`, `credit_cost`, `provision_coverage`
- `loan_growth`, `deposit_growth`, `casa_share`
- `fee_income_growth`
- `ex_one_off_disclosure` (underlying growth excluding one-offs such as the FCNR(B) swap window; cheat sheet: "always ask for the ex-one-off numbers")
- `pre_result_business_update` (does the call reference the earlier provisional deposits/advances update)
- ADD? `capital_ratios` (CET-1 / CRAR; the cheat sheet RBL walkthrough uses them as context)

**NBFCs** (Bajaj Finance, Shriram, Cholamandalam, M&M Fin and similar)
- `aum_growth`, `disbursement_momentum`
- `nim`, `cost_of_funds`
- `credit_cost`, `slippages` (flag unsecured and microfinance segment commentary)
- `fee_income_crosssell`
- `regulatory_commentary` (RBI/IRDAI draft rules named by management as margin risks)

**FMCG / staples** (HUL, ITC, Nestle, Dabur, Marico, Britannia and similar)
- `volume_growth` and `price_growth` (stored separately: volume is real demand, price-led growth is "rented")
- `gross_margin`
- `rural_vs_urban_commentary`
- `base_effect_commentary`
- `next_quarter_festive_commentary`
- ITC only, via segment slots: `segment_commentary` (read segments, not the consolidated headline)

**Autos** (Maruti, M&M, Tata Motors, TVS, Bajaj, Hero, Eicher and similar)
- `volume_growth` (wholesale), `retail_vs_wholesale_commentary`
- `segment_mix` (PV / 2W / CV / tractor, never averaged together)
- `premiumisation_ev_mix` (SUV and EV share, realisation)
- `commodity_cost_vs_price_hikes` (margin bridge)
- `ev_investment_drag`
- `export_and_supply_chain_commentary`

**Pharma** (Sun, Dr Reddy's, Cipla, Lupin, Zydus, Aurobindo and similar)
- `us_generics_price_erosion`, `one_product_cliff_commentary` (e.g. a known exclusivity anniversary)
- `india_formulations_vs_ipm` (growth vs market, chronic vs acute)
- `specialty_new_product_revenue`
- `rd_sga_trajectory`
- `regulatory_usfda_headlines`

**Metals and commodities** (Tata Steel, JSW, SAIL, Hindalco, Vedanta and similar)
- `realisation_vs_benchmark` (domestic HRC, LME aluminium)
- `spread_realisation_minus_input_cost`, `ebitda_per_tonne` (steel)
- `volume_growth`
- `debt_and_capex_cycle`

**Value retail and consumer discretionary** (DMart, Trent, Vedant Fashions and similar)
- `ssg_or_proxy` (same-store growth where given; otherwise store adds and revenue per store)
- `store_adds`, `total_stores`
- `ebitda_margin_commentary` (30bps is a big move here)
- `opex_wage_commentary`
- `store_opening_guidance`, `ecommerce_quickcommerce_drag`
- `reporting_basis` (standalone vs consolidated, whichever management quotes)

**Industrials and capital goods** (L&T, Siemens, ABB, BEL and similar)
- `order_inflow`, `order_book`
- `execution_margin`, `working_capital`
- `private_vs_govt_capex_commentary`

### 3.4 Sector assignment and fallback
- Add a `sector_map` config (symbol -> sector key from the list above). Source it once, by LLM-assisted classification of the 213 names reviewed by Adarsh, then commit the map as a plain config file. Do not classify at extraction time.
- A symbol whose sector has no card above (the F&O universe will include oil and gas, power, cement, telecom, insurance, real estate, chemicals, capital markets and others) gets `sector = UNMAPPED`: extraction runs **universal-only** (10 columns + frame block), the sector block is empty, and the row is flagged `sector_unmapped = true`. Show a visible "universal-only" tag in the UI. Do not guess slots for uncovered sectors.
- PSU banks: treat them under "Private banks" slots (same metrics) but record `bank_type = PSU` as map metadata. **[OWNER DECISION]** confirm.
- Conglomerates (Reliance, Tata group holdcos, ITC): the map holds one primary sector plus optional `secondary_sectors`. v1 fills the primary only. **[OWNER DECISION]** whether to fill segment slots for conglomerates later.

### 3.5 [OWNER DECISION] sector-slot additions beyond the cheat sheet
The slot lists above follow the cheat sheet. Three marked `ADD?` are proposed extras (AI revenue for IT, capital ratios for banks). Also proposed: **new sector cards** for sectors the cheat sheet does not cover, since an F&O universe has many such names (oil and gas, power/utilities, cement, telecom, insurance, real estate, chemicals, capital markets). Default: **none added**, those names run universal-only until Adarsh writes or approves the cards. Build the sector map so adding a card is a config addition, not a code change.

### 3.6 How slots are extracted
Slots are filled in stage 2 alongside the other fields, in the same LLM pass, using a per-sector slot list injected into the prompt. The prompt must say: fill a slot only with something management actually said; a number must be quoted with its unit and basis; if not said, return `NOT_GIVEN`. The same verbatim-quote substring check from 2.3 applies to every `GIVEN` slot.

Transcript-only note: some metrics (TCV, NIM, GNPA) are also in press releases and XBRL filings. In this spec, **slots come from the transcript only** (NSE source lock). A TCV figure that is in the press release but not said on the call is `NOT_GIVEN` here, not filled from the release. Structured actuals from filings are a separate feature pipeline.

### 3.7 Storage shape
Table `earnings_sector_slots`, one row per `(symbol, quarter, slot_key)`:
`symbol, quarter, sector, slot_key, status, value_text, value_num, unit, period_basis, quote, exchange_ref, char_start, char_end, extractor_version, source_pdf_sha256, cleaned_text_sha256` plus the three timestamps in section 5. Long format (one row per slot) so adding a sector card never changes the schema.

---

## 4. Guidance: the settled rules

Guidance has **two parts kept strictly separate**. The `GUIDANCE` cell shows both in one cell; the structured tables hold them separately.

### 4.1 Formal guidance
What the company explicitly states as a forward target: a number, band, or explicit direction for a defined period (e.g. "FY27 operating margin 24 to 26%", "revenue growth in double digits for FY27", an order-inflow target).
- `formal_status`: one of `GIVEN`, `NOT_GIVEN`, `WITHDRAWN_OR_CHANGED`, `NOT_VERIFIED`.
- `NOT_GIVEN` is a **first-class state**, never a blank. A company that says "we do not give guidance" is `NOT_GIVEN`, and the quote saying so is stored as evidence.
- For each of the four generic fields (`guidance_revenue`, `guidance_margin`, `guidance_capex`, `guidance_orderbook`) store: status, value text, period it applies to, and evidence. Fields not stated are `NOT_GIVEN`.
- If the guidance changed from the prior quarter's call, set `WITHDRAWN_OR_CHANGED` and store the old and new values.

### 4.2 Soft outlook
Qualitative or non-committal forward statements ("we see demand stabilising", "we are cautiously optimistic on H2"). Stored as a list of items: `{topic (which of the four fields or a sector slot it informs), direction (positive / neutral / negative / mixed), quote, exchange_ref}`.
- Soft outlook is **never** promoted to formal guidance, and never fills a formal field. This is the whole reason for the split.
- A company can have formal `NOT_GIVEN` and a rich soft outlook (that is the TCS Q2 FY27 pattern).

### 4.3 Traceability
Every non-empty value has: a verbatim quote, exchange number or "prepared", and character offsets. Checked by the substring rule in 2.3. A value without a passing quote is rejected.

### 4.4 The `GUIDANCE` cell text (main sheet)
Format as two labelled parts in one cell: `FORMAL: <status, and value if given> | SOFT: <one-line summary of soft outlook>`. Example shape: `FORMAL: NOT GIVEN | SOFT: positive on deal pipeline, cautious on discretionary`. No example stock is baked into the extraction prompts: the prompts stay generic.

---

## 5. Timestamp separation (storage only, no model)

Every extracted feature row, in every table, carries three timestamps in IST ISO-8601 with offset:

- `announcement_at`: when the company filed its results on NSE (the results-print instant). Source: the NSE results filing timestamp. If only a date is known, store the date and set `announcement_at_precision = "date"`.
- `source_published_at`: when the source document (the transcript PDF) was published on NSE.
- `feature_available_at`: the earliest moment this feature could have been known to a market participant. For transcript-derived features this equals `source_published_at`. For a feature built from several sources, it is the **latest** of the sources' publish times.

Rules:
- Store all three on every row, even when equal. Never overwrite, never derive them at read time.
- **Why:** a transcript is published after the results print, so transcript features did not exist at the moment of the announcement move. Any later analysis must be able to filter on `feature_available_at` strictly before or after `announcement_at`. Keep this a stored fact so the dataset stays honest.
- If the transcript's publish time cannot be established from NSE, store `source_published_at = NULL` and `timestamp_quality = "UNKNOWN"`. A row with unknown timing is excluded from any time-sensitive use. Never default it to the announcement time.
- Also store `extracted_at` (when we ran the extraction), separate from the three above.

---

## 5A. Estimate vs actual: SURPRISE

Surprise is a separate deterministic comparison, not a transcript-extracted field. It implements the universal frame's check 1 (vs consensus), works even when no transcript exists, and adds no columns to the locked 10-column transcript sheet.

### 5A.1 Input: the sheet Adarsh uploads
**Adarsh will upload a sheet into the project's data folder. Read that sheet for estimates. No estimate fetching, scraping or external estimate integration is part of this build.** Inspect its headers and map them explicitly to this minimal import contract; support its existing wide layout by normalizing internally rather than asking him to rebuild the sheet:

| field | meaning |
|---|---|
| `symbol`, `period_end_date` | stock and exact reporting quarter |
| `metric`, `estimate_value` | metric identity and numeric estimate; missing stays NULL |
| `currency`, `unit_scale` | currency and scale, including per-share units where relevant |
| `snapshot_at` | original estimate capture time with timezone; NULL if unknown, never the upload time |
| `basis` | consolidated/standalone, parent/group profit and basic/diluted EPS as applicable; distinguish VERIFIED from INFERRED/UNKNOWN |
| `snapshot_verification` | VERIFIED_PRE_PRINT / HISTORICAL_UNVERIFIED_FROZEN / UNKNOWN |

Keep the original file hash and row reference. Reimporting the same file is a no-op; a changed upload makes a new version rather than overwriting an old input. If the sheet is absent, show `AWAITING_UPLOAD`. Missing metadata stays unknown, never silently invented. Default estimate basis is "probable consolidated, parent-attributable", explicitly INFERRED, not verified. If multiple snapshots exist, use the latest demonstrably before `announcement_at`; reject post-print snapshots as pre-print estimates.

### 5A.2 Actuals: NSE XBRL
Use the desk's NSE `integrated-filing-results` / linked XBRL actuals interface, with the filing URL, period, units and accounting basis retained. Reuse existing verified actuals code; this section does not independently authorize a new collector build. Missing actuals show `ACTUAL_PENDING`, not zero.

Store both `ProfitLossForPeriod` (group including non-controlling interests) and `ProfitOrLossAttributableToOwnersOfParent`. PAT surprise uses parent-attributable profit, not group PAT. EPS comes from the XBRL per-share concept, with basic/diluted basis, never PAT divided by shares. Match discrete quarterly facts, not cumulative YTD values. Bank taxonomy needs explicit mapping: do not assume the standard EPS concept exists; an unmapped fact stays NULL.

### 5A.3 Calculation and guards
For each comparable metric separately:

`surprise_pct = 100 * (actual - estimate) / abs(estimate)`

Retain estimate, actual, raw delta and the linked input versions. Match period, metric definition, currency/scale and accounting basis first. Known mismatches suppress the percentage as `NOT_COMPARABLE`; unknown/inferred basis makes the comparison visibly `PROVISIONAL`.

- Missing inputs give NULL with a named reason, not 0%.
- Zero/near-zero estimates suppress the percentage as `NEAR_ZERO_ESTIMATE`; use documented metric/unit-specific tolerance, not an arbitrary hidden constant.
- Sign flips around zero show `SIGN_FLIP`, both signed inputs and raw delta instead of a misleading headline percentage.
- EPS must be on the same split/bonus-adjusted share basis. Retain adjustment evidence; unresolved actions suppress EPS surprise. Do not apply per-share factors to revenue/PAT.
- Banks: revenue surprise is **NII-to-NII only**; total income or undefined revenue is not comparable. Bank EBITDA is always NULL.

Store `estimate_snapshot_at` (mapped from the sheet's `snapshot_at`), `announcement_at`, `actual_filed_at`, and calculation/import times separately. Unknown/date-only timing cannot prove pre-print availability. Surprise `feature_available_at` is the later availability of its inputs, or UNKNOWN if unavailable. Preserve input versions if filings are corrected.

A sheet row marked HISTORICAL_UNVERIFIED_FROZEN must display **"Historical estimate: not verified as frozen before results; may have been restated."** A verified actual does not remove that flag. Clean snapshot provenance still needs all comparability checks above.

### 5A.4 UI and checks
Show the stored comparisons in the per-stock results view only (no universe-wide day view): per-metric estimate, actual, raw delta, surprise %, units/basis and quality labels, separate from formal guidance and promised-vs-delivered. Include prints without transcripts; pending/suppressed values are named states. Provisional and historical-unverified rows are clearly labelled.

Test normal inputs, missing/near-zero values, sign flips, basis mismatches, bank NII and EBITDA rules, bank EPS mapping, split/bonus EPS, post-print snapshot rejection, historical-unverified labels and corrected filings. Displayed numbers must match the stored comparison records.

---

## 6. UI: the Guidance tab

A tab in the per-stock view, alongside the existing sentiment/stock view. Design language: Swiss Editorial / Charcoal Warm (charcoal background, warm off-white ink, amber accent, green for verdict-positive, orange reserved for losses per the design doc). Clean, data-dense but calm. Do not redesign other tabs.

Top to bottom, one desktop view:

1. **Header row.** Stock symbol and name, sector tag (or a muted "UNIVERSAL ONLY" tag if unmapped), quarter selector (default: latest quarter, switchable to any backfilled quarter; this is how Adarsh sees "previous earnings too"). Two status chips: `FORMAL: GIVEN / NOT GIVEN` and `SOFT OUTLOOK: PRESENT / ABSENT`.
2. **Left panel: GUIDANCE FIELDS.** Four generic cells in a 2x2: Revenue, Operating margin, Capex, Order book. Each cell shows: field label (small caps), current-quarter value in large editorial type, or an em-dash with a muted **NOT GIVEN** label (a deliberate designed state, same size as a value, not a hidden cell), the period it applies to, and a thin "prior quarter" line inside the cell (previous quarter's value for continuity, or "NOT GIVEN last quarter"). `NOT_VERIFIED` renders as a distinct hatched/outlined state labelled NOT VERIFIED, never as NOT GIVEN.
3. **Below the 2x2: SECTOR METRICS.** The sector slots for this stock's sector as a compact table: slot label, value, basis (QoQ/YoY/LTM), prior quarter value, and a small delta arrow where both are numeric. Same NOT GIVEN and NOT VERIFIED states per row. Section title shows the sector, e.g. "IT SERVICES METRICS". Unmapped stock: this panel shows a single line "No sector card for this company yet. Universal fields only."
4. **Right panel: SOFT OUTLOOK.** Verbatim management quotes, each tagged with the field or slot it informs and a direction marker, plus the exchange reference. Strictly separate from the formal fields.
5. **Bottom strip, full width: PROMISED vs DELIVERED.** Compares the **prior** quarter's formal guidance (and any numeric sector commitments) against this quarter's reported/stated value. Columns: field, what was promised (with date of call), what was delivered, verdict. Verdicts: `IN BAND`, `ABOVE`, `BELOW`, `NO PROMISE TO TEST`, `NOT COMPARABLE` (basis mismatch). Only numeric promises with a comparable actual get an in-band verdict. The delivered value comes from the next transcript's own statement or the XBRL actuals feature if the desk has it; show which source it used. Footer line: when the next check runs (next quarter's results).
6. **History strip (the backfill view).** A compact row of the last N quarters (N = backfill depth) showing, per quarter: formal status, margin guidance value, tone score, divergence grade, evasive count. Click a quarter to load it into the panels above.

**SURPRISE companion panel.** In the per-stock results view, show the stored metric comparisons and record IDs (section 5A), including pending states, basis and historical-unverified caveats. Keep this separate from formal guidance and promised-vs-delivered: consensus surprise and delivery against management's promise are different tests. Do not add fields to the transcript sheet.

Drill-down: every value in the panel has a small "evidence" affordance opening the verbatim quote, exchange number, and the NSE PDF link. Provenance is visible, not buried.

---

## 7. Backfill plan

### 7.1 Goal
Run the section 2 to 4 extraction over past quarters so the dataset exists across the F&O universe, same code path as live (not a separate script), with the section 5 timestamps populated.

**Surprise backfill:** independently import the uploaded sheet's historical estimates and join available NSE actuals by symbol/period/metric (section 5A). Preserve historical verification flags and missing-data states; transcript availability does not gate surprise.

### 7.2 How many quarters back **[OWNER DECISION]**
- **Recommended: 6 quarters** for phase 1. Reasons: it matches the 6-quarter pattern the desk already uses and the v3 spec was validated on exactly that; 6 gives the latest quarter plus year-ago comparison for the two most recent quarters and enough prior-quarter continuity for the promised-vs-delivered strip; it keeps cost modest while the sector slots are still unproven.
- **Option: 8 quarters** gives two full year-over-year cycles and is a better base for any later statistics. **Option: 12** is a stronger base but sector definitions and company disclosure formats drift over three years, so it needs more QA.
- Build backfill depth as a single config value `BACKFILL_QUARTERS`. Phase 2 can extend deeper without code changes. Adarsh decides the final depth after seeing phase 1 cost and quality.
- Some names will have fewer transcripts on NSE (new listings, names that did not file). Record `transcript_available = false` for that quarter; do not fill from another source.

### 7.3 Order of processing
1. **Upcoming-earnings names first** (names with an announced results date in the next stretch; the desk already knows these, 77 of 188 had dates at last check). Reason: these are the ones he will look at soonest, and prior-quarter history is what the promised-vs-delivered strip needs the moment they report.
2. **Then by Nifty size tier**: Nifty 50 constituents, then Nifty Next 50, then the rest of the F&O universe by market cap descending.
3. **Within a name, newest quarter first, then walk backward.** The latest quarter is the most useful if the run stops early.
4. Names with `sector = UNMAPPED` still run (universal-only) in the same order.

### 7.4 Batching
- Batch of 10 names at a time, all quarters for each name in the batch, sequentially per name so one failure stays contained.
- Idempotent: key `(symbol, quarter, source_pdf_sha256, extractor_version)`. Re-running skips completed rows; a new `extractor_version` re-extracts.
- Persist progress after each name. A crashed batch resumes from the last completed `(symbol, quarter)`.
- Rate limit NSE requests politely (one in flight, delay between requests, honour 429s with backoff, stop on repeated blocks). On a block, stop and report; do not rotate networks or evade the block.
- After each batch write a summary: names done, quarters done, rows by status (`GIVEN / NOT_GIVEN / NOT_VERIFIED / EXTRACTION_FAILED`), `transcript_available = false` count, token spend.
- **Pilot first:** run the first batch on Nifty 50 names only and stop for review (see section 8) before continuing to the full universe.

### 7.5 NSE fetch mechanics
- NSE serves corporate-filing attachments from the `nsearchives.nseindia.com` host. The transcript for a call is a PDF attached to a corporate announcement (subject about "Transcript of earnings/analyst call" or similar).
- **Discovery:** find the announcements for a symbol via NSE's corporate-announcements listing for that symbol and date range, filter to transcript filings, and read each one's attachment link and its broadcast/disclosure timestamp (this timestamp is `source_published_at`). Treat the listing endpoint and field names as **to be verified by you against live responses**; I have not verified their current shape, so do not hard-code from memory.
- **Reference examples:** six verified NSE transcript URLs for TCS (calls between 14 Jul 2025 and the 14 Oct 2026 season) are known from earlier extraction work. Ask Adarsh for them or reuse them from the existing TCS extraction files; use them to confirm the URL shape and the listing-to-PDF mapping before building the general fetcher.
- NSE sits behind session/cookie checks: use a normal browser-like session (load the NSE site first for cookies, set a proper user agent), cache the session, and respect rate limits. If NSE blocks, stop and report.
- **Download rules:** save the original PDF bytes untouched to a local archive directory (not the repo), compute SHA-256 immediately, record URL, fetch time, byte size, and HTTP status. The hash is the identity of the source for the whole pipeline.
- **If a filing is missing or the PDF is corrupt/unreadable:** status `NOT_VERIFIED` for all fields of that quarter, `transcript_available` false or `pdf_unreadable` true, never filled from a mirror.
- **Quarter assignment:** derive the fiscal quarter from the call date and the company's reporting period as stated in the filing (Indian FY runs April to March; "Q2 FY27" is the quarter ended 30 Sep 2026). Store `fiscal_quarter` and `period_end_date`. Where a company's call covers more than one period, flag for review rather than guessing.

### 7.6 PRICE MOVE for historical quarters
Same definition as live: next-session close vs results-day close. Needs `announcement_at` to pick the right session. If results were filed after market close, the "results-day close" is the close before the reaction, and the next session carries the reaction; use the exact v3 definition and record both dates used. Use the desk's existing price source; do not add a new one. Adjust for splits and bonuses using the adjusted-close series, never raw closes across a corporate action.

### 7.7 LLM cost estimate
Estimates only, to be replaced with measured numbers from the pilot batch.
- Assumed per transcript: ~25k input tokens of text. Stage 1 (segmentation): ~25k in, ~3k out. Stage 2 (extraction incl. sector slots): ~28k in, ~6k out. Total about **53k in, 9k out** per name-quarter.
- Illustrative price assumption of $3 per million input tokens and $15 per million output tokens (the model and price are not fixed; recompute with the model actually used): about **$0.30 per name-quarter**.
- 213 names x 6 quarters = 1,278 name-quarters: roughly **$380** at that price. 8 quarters: about $510. 12 quarters: about $770. Subtract missing transcripts and add 10 to 15% for retries and reruns.
- Pilot (Nifty 50 x 6 quarters = 300 name-quarters) is about $90 at the same price. Log tokens in/out per call so the real number replaces this estimate after the pilot.
- Cheaper levers if needed, to be decided with Adarsh: a smaller model for stage 1 only, caching the cleaned text between stages. Do not change the stage 2 model without a quality re-check on the TCS reference set.

---

## 8. QA and acceptance

Build is not done until all of these pass.

1. **TCS 6-quarter regression.** Re-run the new pipeline on the TCS 6-quarter reference set and confirm the 10 columns reproduce the validated v3 behaviour: graded divergence N/MILD/N/MILD/MILD/MILD across the six quarters; evasive counts 1, 3, 1, 1, 5, 8; GUIDANCE cell in the formal-plus-soft format; tone on the -2 to +2 scale with exchange cites. Any difference is reported, not silently accepted.
2. **Quote check.** 100% of `GIVEN` values and cites pass the exact-substring test (2.3).
3. **Schema lock check.** The 10-column sheet is byte-for-byte the same column set and order as specified in section 1.
4. **Sector pilot spot-check.** Run one name per mapped sector (9 names). For each, print the filled slots with quotes so Adarsh can verify that slots are being filled from what was said and that `NOT_GIVEN` is returned when the call is silent. Report fill rate per slot; a slot that is almost always `NOT_GIVEN` across a sector is a signal to review that slot, not to loosen the rule.
5. **Unmapped path.** One unmapped name runs universal-only, flagged, with no sector rows.
6. **Timestamps.** Every row in every table has the three timestamps and `extracted_at`. A test fails the build if any row is missing one (NULL allowed only with `timestamp_quality = "UNKNOWN"`).
7. **Idempotency.** Re-running a batch changes nothing and spends no tokens on completed rows.
8. **UI.** Guidance tab renders for a mapped stock, an unmapped stock, a stock with a `NOT_VERIFIED` quarter, and a stock with no prior quarter (promised-vs-delivered shows NO PROMISE TO TEST).
9. **Pilot gate.** After the Nifty 50 pilot, stop and report: rows by status, fill rate per sector slot, failures, measured cost per name-quarter, and 5 randomly chosen rows with quotes. Do not continue to the full universe until Adarsh says go.

---

## 9. Build order

1. Storage: the three tables (`earnings_frame`, `earnings_sector_slots`, existing 10-col table) with timestamps and the status enum. No data yet.
2. `sector_map` config and the slot-card config (a data file, one card per sector from 3.3).
3. NSE fetcher + hasher + archive, proven on the six known TCS URLs.
4. Extraction: port v3 mechanics unchanged, add the frame block and sector slots to stage 2, add the substring verifier.
5. TCS regression (8.1) and the nine-sector spot-check (8.4).
6. Guidance tab UI (section 6) against the real TCS data.
7. Backfill driver with the pilot gate (section 7 and 8.9).

## 10. Decisions that are Adarsh's (collected)

1. Backfill depth: 6 (recommended), 8, or 12 quarters (7.2).
2. Extra slots beyond the cheat sheet: AI revenue (IT), capital ratios (banks), and new sector cards for uncovered sectors such as oil and gas, power, cement, telecom, insurance, real estate, chemicals, capital markets (3.5). Default is none.
3. PSU banks under the private-bank card, with a PSU flag (3.4).
4. Conglomerates: primary sector only for now, segment slots later (3.4).
5. Where the populated dataset lives. Default is local storage, code only in the repo.
6. Continue from the Nifty 50 pilot to the full universe (7.4, 8.9).
