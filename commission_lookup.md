# Motor Insurance Commission Lookup System
## Complete Technical Documentation

**Version:** 1.0  
**Scope:** Commission query engine — from user input to rate resolution  
**Insurers covered:** DigiSafe (Go Digit), Chola MS, and any insurer with a schema profile  

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Schema Changes Required](#3-schema-changes-required-to-the-json-profile)
4. [Database Schema](#4-database-schema)
5. [User Input Form — Field Definitions](#5-user-input-form--field-definitions)
6. [Lookup Resolution Logic](#6-lookup-resolution-logic)
7. [API Design](#7-api-design)
8. [Code — Backend (Python/FastAPI)](#8-code--backend-pythonfastapi)
9. [Code — Frontend (React)](#9-code--frontend-react)
10. [RTO → Cluster Mapping](#10-rto--cluster-mapping)
11. [Edge Cases and Special Rules](#11-edge-cases-and-special-rules)
12. [Testing Strategy](#12-testing-strategy)
13. [Deployment Notes](#13-deployment-notes)

---

## 1. System Overview

The commission lookup system answers one question:

> "Given a specific vehicle and policy being sold today, what commission percentage does our broker earn from each insurer?"

It works in three layers:

```
User fills form  →  Backend resolves dimensions  →  DB matches commission rules
```

The system is **profile-driven**: every insurer has a `schema_profile` in the database that describes how its commission grid is structured. The lookup engine reads these profiles at query time to know which dimensions matter, how clusters are defined, and what special rules apply.

### Key Design Principles

- **Multi-insurer by default.** A single query returns commission rates from all active insurers for the same vehicle/policy combination, ranked by rate.
- **Graceful degradation.** If an exact match is not found, the engine relaxes dimensions one at a time (partial match) and flags which dimensions were relaxed.
- **Audit trail.** Every query is logged to `lookup_logs` — agent ID, all input params, rate returned, match quality, and resolution time.
- **No hardcoded insurer logic in the query engine.** All branching comes from the profile JSON.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          FRONTEND (React)                               │
│                                                                         │
│   CommissionQueryForm                                                   │
│   ├── VehicleSection  (category, make, model, reg no, mfg date)        │
│   ├── PolicySection   (coverage, case type, NCB%, add-ons)             │
│   ├── InsurerSelector (All / specific insurer / best-of-all)           │
│   └── ResultsPanel    (rate table, match quality badges, audit trail)  │
└────────────────────────────┬────────────────────────────────────────────┘
                             │  HTTPS / REST
┌────────────────────────────▼────────────────────────────────────────────┐
│                     API GATEWAY (FastAPI)                               │
│                                                                         │
│   POST /api/v1/commission/query                                         │
│   GET  /api/v1/commission/insurers                                      │
│   GET  /api/v1/commission/vehicle-meta?reg_no=...                       │
│   GET  /api/v1/commission/rto-cluster?rto_code=...&insurer_id=...       │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
┌─────────▼──────┐  ┌────────▼───────┐  ┌──────▼──────────┐
│  DimensionResolver│  │ RuleMatchEngine │  │  ProfileLoader  │
│                │  │                 │  │                 │
│ • Reg No→RTO   │  │ • Exact match   │  │ • Fetches JSONB │
│ • RTO→Cluster  │  │ • Partial match │  │   from DB       │
│ • MfgDate→Age  │  │ • Specificity   │  │ • Caches 5 min  │
│ • CC→Segment   │  │   ranking       │  │                 │
│ • NCB mapping  │  │ • Special rules │  │                 │
└────────────────┘  └────────────────┘  └─────────────────┘
          │                  │
┌─────────▼──────────────────▼──────────────────────────────┐
│                  PostgreSQL 16                             │
│                                                           │
│  insurers                commission_grids                 │
│  insurer_schema_profiles  commission_rules                │
│  ingestion_batches        rule_dimensions                 │
│  rto_master               lookup_logs                     │
│  vehicle_make_model       cluster_rto_mappings            │
└───────────────────────────────────────────────────────────┘
          │
┌─────────▼──────────┐
│   Redis Cache       │
│  • Active grids    │
│  • Profile cache   │
│  • RTO→cluster map │
└────────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|---|---|
| `ProfileLoader` | Reads `insurer_schema_profiles` from DB. Caches for 5 minutes. Single source of truth for dimension names, cluster maps, special rules. |
| `DimensionResolver` | Converts raw user inputs (reg no, mfg date, CC value) into canonical dimension values (cluster, age band, segment key). |
| `RuleMatchEngine` | Queries `commission_rules` + `rule_dimensions` using resolved dimensions. Tries exact match first, then relaxes. |
| `ResultsAggregator` | When `best_of_all=true`, runs the engine for each active insurer and collates results. |

---

## 3. Schema Changes Required to the JSON Profile

The current `DIGISAFE_SCHEMA_PROFILE` (and `CHOLA_SCHEMA_PROFILE`) must be extended with the following new top-level keys to support the query engine. **No existing keys need to change.**

### 3.1 `query_dimensions` — maps user form fields to profile dimension keys

```json
"query_dimensions": {
  "vehicle_category_field": "segment",
  "fuel_type_field": "fuel_type",
  "coverage_field": "policy_type",
  "ncb_field": "ncb_flag",
  "age_field": "age",
  "cluster_field": "cluster",
  "seating_capacity_field": "seating_capacity",
  "gvw_field": "gvw_subclass",
  "cc_field": "cc_range"
}
```

This tells the query engine: "when the user submits `fuel_type=diesel`, look for a `rule_dimension` row where `dimension_key = 'fuel_type'` and `dimension_value = 'diesel'`."  
Each insurer's profile maps its own internal dimension key names here.

### 3.2 `coverage_to_policy_type` — maps the UI radio value to the profile's policy_type values

```json
"coverage_to_policy_type": {
  "OD": ["saod", "od_only"],
  "TP": ["tp_only", "satp"],
  "COMP": ["comp", "package", "comprehensive"]
}
```

The UI shows `OD / TP / COMP`. Each insurer may call these differently internally. This mapping normalises them.

### 3.3 `case_type_rules` — how case type affects which grid to read

```json
"case_type_rules": {
  "NEW": { "use_grid": "active" },
  "RENEWAL": { "use_grid": "active", "note": "Same grid; NOP slab may differ for Chola TW" },
  "ROLLOVER": { "use_grid": "active", "apply_ncb": false },
  "BREAKIN": { "use_grid": "active", "apply_ncb": false, "flag": "needs_review" }
}
```

### 3.4 `ncb_dimension_map` — maps NCB % to the dimension value used in rules

Different insurers encode NCB differently. Chola uses `ncb_flag: with_NCB / without_NCB`. DigiSafe 4W Comp uses explicit NCB % columns.

```json
"ncb_dimension_map": {
  "type": "flag",
  "flag_threshold_pct": 0,
  "values": {
    "0": "without_NCB",
    "20": "with_NCB",
    "25": "with_NCB",
    "35": "with_NCB",
    "45": "with_NCB",
    "50": "with_NCB"
  }
}
```

For insurers that have per-NCB-slab columns, set `"type": "slab"` and list the breakpoints.

### 3.5 `addon_deductions` — CPA / Zero Dep effect on commission

```json
"addon_deductions": {
  "CPA_not_collected": {
    "deduction_pct": 1.5,
    "applies_to": ["PC_PACK", "PC_ACT", "TW_PACK"]
  },
  "zero_dep_addon": {
    "effect": "none",
    "note": "Zero Dep is an add-on premium — does not change commission % for DigiSafe"
  }
}
```

### 3.6 `vehicle_category_to_segment` — maps UI vehicle category dropdown to profile segment keys

```json
"vehicle_category_to_segment": {
  "2W_BIKE":       { "segment_key": "mc_lte155", "product_line": "TW" },
  "2W_SCOOTER":    { "segment_key": "scooter",   "product_line": "TW" },
  "PC":            { "segment_key": null,          "product_line": "PC", "resolve_from": "cc_and_fuel" },
  "TAXI":          { "segment_key": "pcv_3w_nondiesel", "product_line": "CV" },
  "SCHOOL_BUS":    { "segment_key": null, "product_line": "CV", "use_sheet": "School Bus - Prime Brokers" },
  "STAFF_BUS":     { "segment_key": null, "product_line": "CV", "use_sheet": "Staff Bus Grid" },
  "GCV_LIGHT":     { "segment_key": null, "product_line": "CV", "resolve_from": "gvw" },
  "GCV_HEAVY":     { "segment_key": null, "product_line": "CV", "use_sheet": "HCV" },
  "MISC":          { "segment_key": "misc_d", "product_line": "CV" }
}
```

### 3.7 `rto_cluster_source` — tells the engine where to look for RTO→cluster mapping

```json
"rto_cluster_source": {
  "table": "cluster_rto_mappings",
  "filter_column": "insurer_id",
  "rto_column": "rto_code",
  "cluster_column": "cluster_name"
}
```

### Summary of Profile Changes

| Key Added | Purpose | Required for Query? |
|---|---|---|
| `query_dimensions` | Maps form fields → dimension_key in rules | **Yes** |
| `coverage_to_policy_type` | Normalises OD/TP/COMP → insurer-specific policy_type | **Yes** |
| `case_type_rules` | Governs RENEWAL/BREAKIN/ROLLOVER behaviour | Yes |
| `ncb_dimension_map` | Maps NCB% → dimension value | **Yes** |
| `addon_deductions` | CPA/ZD deduction amounts | Yes |
| `vehicle_category_to_segment` | Maps UI category → segment dimension | **Yes** |
| `rto_cluster_source` | Points engine to RTO→cluster table | **Yes** |

---

## 4. Database Schema

### 4.1 New tables (additions to the existing schema)

```sql
-- ─────────────────────────────────────────────────────────────────
-- RTO Master  — source of truth for RTO code → state → region
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE rto_master (
    rto_code        VARCHAR(10)  PRIMARY KEY,    -- e.g. "MH01", "KA05"
    rto_name        TEXT         NOT NULL,        -- e.g. "Mumbai Central"
    state_code      VARCHAR(4)   NOT NULL,        -- e.g. "MH"
    state_name      TEXT         NOT NULL,
    city            TEXT,
    region          TEXT,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rto_state ON rto_master(state_code);

COMMENT ON TABLE rto_master IS
  'Canonical RTO code list. Populated from VAHAN/Parivahan data. '
  'Used to resolve registration numbers to state and region.';

-- ─────────────────────────────────────────────────────────────────
-- Cluster ↔ RTO Mapping  — insurer-specific cluster definitions
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE cluster_rto_mappings (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    insurer_id      UUID         NOT NULL REFERENCES insurers(id) ON DELETE CASCADE,
    cluster_name    VARCHAR(100) NOT NULL,    -- e.g. "Guj_Good", "ROM1", "Delhi_NCR"
    rto_code        VARCHAR(10)  REFERENCES rto_master(rto_code),
    state_code      VARCHAR(4),              -- when mapping is at state level
    quality_cohort  VARCHAR(20),             -- good / bad / ref / null
    source          VARCHAR(30)  NOT NULL DEFAULT 'profile',  -- 'profile' or 'manual'
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cluster_rto_insurer ON cluster_rto_mappings(insurer_id, rto_code);
CREATE INDEX idx_cluster_rto_state   ON cluster_rto_mappings(insurer_id, state_code);

COMMENT ON TABLE cluster_rto_mappings IS
  'Maps RTO codes (and state codes) to insurer-specific cluster names. '
  'Populated during ingestion from the profile cluster_geography_map. '
  'One RTO can map to multiple clusters if the insurer splits by quality cohort.';

-- ─────────────────────────────────────────────────────────────────
-- Vehicle Make / Model master
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE vehicle_make_model (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    make            VARCHAR(100) NOT NULL,
    model           VARCHAR(200) NOT NULL,
    vehicle_category VARCHAR(30) NOT NULL,   -- 2W_BIKE, 2W_SCOOTER, PC, GCV_LIGHT, etc.
    fuel_type       VARCHAR(20),             -- petrol, diesel, cng, electric, hybrid
    cc_min          INT,
    cc_max          INT,
    kw_min          NUMERIC(6,2),            -- for EVs
    kw_max          NUMERIC(6,2),
    gvw_kg          INT,                     -- for commercial vehicles
    seating_capacity INT,                    -- for buses / PCV
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_vmm_make    ON vehicle_make_model(make);
CREATE INDEX idx_vmm_cat     ON vehicle_make_model(vehicle_category);
CREATE INDEX idx_vmm_fuel    ON vehicle_make_model(fuel_type);

COMMENT ON TABLE vehicle_make_model IS
  'Master of all insurable vehicle make+model combinations. '
  'Seeded from VAHAN/IIB data. Used for make/model dropdown and '
  'for deriving CC, GVW, seating capacity when not entered manually.';

-- ─────────────────────────────────────────────────────────────────
-- Query Sessions  — groups multi-insurer results for one agent query
-- ─────────────────────────────────────────────────────────────────
CREATE TABLE query_sessions (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID         NOT NULL,
    query_params    JSONB        NOT NULL,   -- the raw form submission
    resolved_dims   JSONB,                  -- after DimensionResolver runs
    insurer_count   INT,                    -- how many insurers were queried
    best_rate       NUMERIC(7,4),
    best_insurer_id UUID         REFERENCES insurers(id),
    queried_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_qs_agent     ON query_sessions(agent_id);
CREATE INDEX idx_qs_queried   ON query_sessions(queried_at DESC);
CREATE INDEX idx_qs_params    ON query_sessions USING GIN(query_params);
```

### 4.2 Additions to existing tables

```sql
-- Add to commission_rules (existing table):
ALTER TABLE commission_rules
  ADD COLUMN IF NOT EXISTS sub_product   VARCHAR(50),   -- e.g. tw_saod, tw_1plus5, pc_comp
  ADD COLUMN IF NOT EXISTS case_types    TEXT[],         -- NEW / RENEWAL / ROLLOVER / BREAKIN
  ADD COLUMN IF NOT EXISTS ncb_pct_min   INT,            -- 0 for no NCB; 20/25/35/45/50
  ADD COLUMN IF NOT EXISTS ncb_pct_max   INT,
  ADD COLUMN IF NOT EXISTS addon_flags   TEXT[];          -- cpa_collected, zero_dep, etc.

-- Add to grid_notes (existing table):
ALTER TABLE grid_notes
  ADD COLUMN IF NOT EXISTS applies_to_case_types TEXT[];  -- which case types this note applies to

COMMENT ON COLUMN commission_rules.sub_product IS
  'Differentiates grids within the same product_line. e.g. tw_saod vs tw_1plus5.
   Populated from sub_product_map in the insurer profile.';

COMMENT ON COLUMN commission_rules.case_types IS
  'Array of case types this rule applies to. NULL = applies to all.
   Values: NEW, RENEWAL, ROLLOVER, BREAKIN.';
```

### 4.3 Updated `lookup_logs`

```sql
-- Extend lookup_logs for session linkage and addon tracking:
ALTER TABLE lookup_logs
  ADD COLUMN IF NOT EXISTS session_id       UUID REFERENCES query_sessions(id),
  ADD COLUMN IF NOT EXISTS sub_product      VARCHAR(50),
  ADD COLUMN IF NOT EXISTS ncb_pct          INT,
  ADD COLUMN IF NOT EXISTS case_type        VARCHAR(20),
  ADD COLUMN IF NOT EXISTS addon_flags      TEXT[],
  ADD COLUMN IF NOT EXISTS final_rate       NUMERIC(7,4),  -- after deductions applied
  ADD COLUMN IF NOT EXISTS deductions_applied JSONB;        -- {cpa: -1.5, ...}
```

---

## 5. User Input Form — Field Definitions

### 5.1 Complete field specification

| # | Field | UI Type | Values / Notes | Maps To |
|---|---|---|---|---|
| 1 | **Issue Date** | Date picker | Default = today | `queried_at` |
| 2 | **Insurer** | Multi-select + checkbox | All active insurers + "Best of All" checkbox | `insurer_id[]` |
| 3 | **Vehicle Category** | Dropdown (required) | 2W Bike, 2W Scooter, Private Car, Taxi, School Bus, Staff Bus, GCV Light, GCV Heavy, Misc | `vehicle_category` |
| 4 | **Make** | Searchable dropdown | Filtered by vehicle_category | `make` |
| 5 | **Model** | Searchable dropdown | Filtered by make | `model` (auto-fills CC / GVW / seating) |
| 6 | **Registration No.** | Text input (optional) | Format: XX00XX0000. Auto-resolves RTO + state + cluster | `reg_no` → `rto_code` → `cluster` |
| 7 | **Manufacturing Date** | Month + Year picker | Used to compute vehicle age | `mfg_date` → `age_years` |
| 8 | **CC / KW** | Number (auto-filled) | CC for ICE; KW for EVs. Editable override | `cc_value` |
| 9 | **Seating Capacity** | Number (conditional) | Shown only for Taxi / School Bus / Staff Bus / PCV | `seating_capacity` |
| 10 | **Gross Weight (GVW)** | Number (conditional) | Shown only for GCV Light / Heavy | `gvw_kg` |
| 11 | **Fuel Type** | Dropdown | Petrol, Diesel, CNG, Electric, Hybrid | `fuel_type` |
| 12 | **Coverage** | Radio | OD, TP, COMP (Comprehensive) | `coverage` |
| 13 | **Case Type** | Radio | NEW, RENEWAL, ROLLOVER, BREAK-IN | `case_type` |
| 14 | **NCB %** | Radio buttons | 0%, 20%, 25%, 35%, 45%, 50% | `ncb_pct` |
| 15 | **CPA Collected** | Checkbox | Checked = CPA premium included | `cpa_collected` |
| 16 | **Zero Dep Add-on** | Checkbox | For reference; affects some insurer deductions | `zero_dep` |

### 5.2 Conditional field visibility rules

```
IF vehicle_category IN [2W_BIKE, 2W_SCOOTER]
    SHOW: cc_value (label: "Displacement (CC)")
    HIDE: gvw_kg, seating_capacity

IF vehicle_category == PC
    SHOW: cc_value (label: "Engine CC")
    HIDE: gvw_kg, seating_capacity

IF vehicle_category IN [TAXI, SCHOOL_BUS, STAFF_BUS]
    SHOW: seating_capacity
    HIDE: gvw_kg, cc_value (unless taxi with CC-based TP grid)

IF vehicle_category IN [GCV_LIGHT, GCV_HEAVY]
    SHOW: gvw_kg (label: "Gross Vehicle Weight (kg)")
    HIDE: cc_value, seating_capacity

IF coverage == OD OR case_type == NEW
    SHOW: ncb_pct (disabled, forced to 0 for new business)

IF case_type IN [ROLLOVER, BREAKIN]
    ncb_pct = 0 (forced), ncb_radio = disabled

IF fuel_type == ELECTRIC
    HIDE: cc_value (label switches to "Motor Power (KW)")
    SHOW: kw_value
```

### 5.3 Auto-fill cascade

```
Registration No input
    → extract state prefix (first 2 chars)
    → lookup rto_master WHERE rto_code = extracted_rto
    → auto-fill: state, region
    → display: "RTO: MH01 – Mumbai Central"

Make + Model selected
    → lookup vehicle_make_model
    → auto-fill: cc_min/max (show midpoint), fuel_type, gvw_kg, seating_capacity
    → user can override any auto-filled value
```

---

## 6. Lookup Resolution Logic

### 6.1 Step-by-step flow

```
Step 1 — Load active profiles
    For each insurer_id in request:
        profile = ProfileLoader.get(insurer_id)
        grid    = latest active commission_grid for (insurer_id, product_line)

Step 2 — Resolve raw inputs → canonical dimensions
    age_years     = today.year - mfg_date.year  (adjust for month)
    rto_code      = parse_reg_no(reg_no)         or user-supplied state
    cluster       = ClusterResolver.resolve(rto_code, insurer_id, profile)
    segment       = SegmentResolver.resolve(vehicle_category, cc, gvw, make, profile)
    fuel_dim      = profile.dimension_aliases.pc_fuel_type[fuel_type]  (or passthrough)
    policy_type   = profile.coverage_to_policy_type[coverage]
    ncb_dim       = profile.ncb_dimension_map.values[str(ncb_pct)]
    sub_product   = derive_sub_product(vehicle_category, coverage, age_years, profile)

Step 3 — Build dimension predicate set
    predicates = {
        "cluster":   cluster,
        "segment":   segment,
        "fuel_type": fuel_dim,
        "policy_type": policy_type,
        "ncb_flag":  ncb_dim,
        "age":       age_years,
        ... (only dimensions relevant to this product_line per profile)
    }

Step 4 — Query commission_rules (exact match first)
    SELECT cr.*, rd.*
    FROM commission_rules cr
    JOIN rule_dimensions rd ON rd.rule_id = cr.id
    WHERE cr.grid_id = :grid_id
      AND cr.is_active = TRUE
      AND cr.sub_product = :sub_product
    GROUP BY cr.id
    HAVING count(*) FILTER (
        WHERE rd.dimension_key = :key AND rd.dimension_value = :value
               ... (one condition per predicate)
    ) = :predicate_count
    ORDER BY cr.match_specificity DESC
    LIMIT 1

Step 5 — If no exact match: partial match (dimension relaxation)
    Relax dimensions in priority order (least specific first):
        1. ncb_flag
        2. fuel_type
        3. age (expand to wider band)
        4. make (fallback to "All")
        5. cluster (fallback to state-level wildcard)
    Log which dimensions were relaxed in lookup_logs.dimensions_relaxed

Step 6 — Apply special rules and deductions
    rate = matched_rule.commission_rate
    IF profile.addon_deductions.CPA_not_collected AND NOT cpa_collected:
        rate -= profile.addon_deductions.CPA_not_collected.deduction_pct
    IF profile.special_rules contains relevant modifiers:
        apply modifier logic (e.g. short-term reduction)

Step 7 — Write to lookup_logs and query_sessions

Step 8 — Return response
```

### 6.2 Sub-product derivation logic

```python
def derive_sub_product(vehicle_category, coverage, age_years, case_type, profile):
    """
    Maps the combination of vehicle_category + coverage + age → sub_product key.
    This determines which sheet/grid to query within a product_line.
    """
    if vehicle_category == "2W_BIKE" or vehicle_category == "2W_SCOOTER":
        if coverage == "OD":
            return "tw_saod"
        if coverage == "COMP" and age_years == 0 and case_type == "NEW":
            # New 2W: check if long-term (1+5 or 5+5) based on policy term input
            # Default to 1+1 comp+satp
            return "tw_new_comp_satp"
        if coverage == "COMP" and long_term_flag == "1+5":
            return "tw_1plus5"
        if coverage == "COMP" and long_term_flag == "5+5":
            return "tw_5plus5"

    if vehicle_category == "PC":
        if coverage == "TP":
            return "pc_tp_only"
        return "pc_comp_saod"

    if vehicle_category == "SCHOOL_BUS":
        return "pcv_school_bus"

    if vehicle_category == "STAFF_BUS":
        return "pcv_staff_bus"

    if vehicle_category == "GCV_HEAVY":
        return "gcv_heavy"

    return "gcv_light"   # GCV_LIGHT, TAXI, MISC, e-rickshaw etc.
```

### 6.3 Cluster resolution

```python
def resolve_cluster(rto_code: str, insurer_id: str, profile: dict) -> str | None:
    """
    1. Look up cluster_rto_mappings for this insurer + rto_code.
    2. If multiple rows (Good/Bad/Ref), return all — let the agent choose cohort,
       OR default to quality_cohort='good' if not specified.
    3. If no RTO-level match, try state_code match.
    4. If still no match, return None and flag as PARTIAL_MATCH.
    """
    rows = db.query(
        "SELECT cluster_name, quality_cohort "
        "FROM cluster_rto_mappings "
        "WHERE insurer_id=:iid AND rto_code=:rto",
        iid=insurer_id, rto=rto_code
    )
    if rows:
        return rows[0].cluster_name   # primary cluster

    # State-level fallback
    state = rto_code[:2]
    rows = db.query(
        "SELECT cluster_name FROM cluster_rto_mappings "
        "WHERE insurer_id=:iid AND state_code=:s",
        iid=insurer_id, s=state
    )
    return rows[0].cluster_name if rows else None
```

---

## 7. API Design

### 7.1 `POST /api/v1/commission/query`

**Request body:**

```json
{
  "issue_date": "2026-03-15",
  "insurer_ids": ["digisafe", "chola"],
  "best_of_all": false,
  "agent_id": "uuid-of-agent",
  "vehicle": {
    "category": "PC",
    "make": "Maruti Suzuki",
    "model": "Swift",
    "reg_no": "KA05AB1234",
    "mfg_date": "2021-06",
    "cc": 1197,
    "fuel_type": "petrol",
    "gvw_kg": null,
    "seating_capacity": null
  },
  "policy": {
    "coverage": "COMP",
    "case_type": "RENEWAL",
    "ncb_pct": 25,
    "cpa_collected": true,
    "zero_dep": false,
    "policy_term": "1+1"
  }
}
```

**Response body:**

```json
{
  "session_id": "uuid",
  "queried_at": "2026-03-15T10:23:11Z",
  "resolved": {
    "rto_code": "KA05",
    "rto_name": "Bangalore South",
    "state": "KA",
    "vehicle_age_years": 4,
    "segment": "petrol_1000_1500cc",
    "sub_product": "pc_comp_saod"
  },
  "results": [
    {
      "insurer_id": "digisafe",
      "insurer_name": "DigiSafe (Go Digit)",
      "commission_rate": 18.5,
      "od_commission_rate": 18.5,
      "tp_commission_rate": null,
      "final_rate": 18.5,
      "deductions_applied": {},
      "payout_basis": "od_plus_addon",
      "match_type": "EXACT",
      "dimensions_relaxed": [],
      "rule_id": "uuid",
      "grid_version": 3,
      "effective_from": "2026-03-01",
      "cluster_used": "Rest_of_KA_Good",
      "notes": ["90:10 rule applies on OD+TP split"]
    },
    {
      "insurer_id": "chola",
      "insurer_name": "Chola MS",
      "commission_rate": 17.0,
      "od_commission_rate": 17.0,
      "tp_commission_rate": null,
      "final_rate": 17.0,
      "deductions_applied": {},
      "payout_basis": "OD_premium",
      "match_type": "PARTIAL",
      "dimensions_relaxed": ["ncb_flag"],
      "rule_id": "uuid",
      "cluster_used": "Rest_of_KA",
      "notes": []
    }
  ],
  "best_result": {
    "insurer_id": "digisafe",
    "commission_rate": 18.5
  }
}
```

### 7.2 `GET /api/v1/commission/vehicle-meta`

```
GET /api/v1/commission/vehicle-meta?reg_no=KA05AB1234

Response:
{
  "rto_code": "KA05",
  "rto_name": "Bangalore South",
  "state_code": "KA",
  "state_name": "Karnataka",
  "region": "South"
}
```

### 7.3 `GET /api/v1/commission/rto-cluster`

```
GET /api/v1/commission/rto-cluster?rto_code=KA05&insurer_id=digisafe

Response:
{
  "rto_code": "KA05",
  "insurer_id": "digisafe",
  "clusters": [
    { "cluster_name": "Rest_of_KA_Good", "quality_cohort": "good" },
    { "cluster_name": "Rest_of_KA_Bad",  "quality_cohort": "bad"  }
  ],
  "default_cluster": "Rest_of_KA_Good"
}
```

### 7.4 `GET /api/v1/commission/makes`

```
GET /api/v1/commission/makes?vehicle_category=PC

Response: ["Maruti Suzuki", "Hyundai", "Tata Motors", "Honda", ...]
```

### 7.5 `GET /api/v1/commission/models`

```
GET /api/v1/commission/models?make=Maruti+Suzuki&vehicle_category=PC

Response: [
  { "model": "Swift", "cc_min": 1197, "cc_max": 1197, "fuel_types": ["petrol", "cng"] },
  { "model": "Baleno", "cc_min": 1197, "cc_max": 1197, "fuel_types": ["petrol"] },
  ...
]
```

---

## 8. Code — Backend (Python/FastAPI)

### 8.1 Project structure

```
commission_lookup/
├── main.py                     # FastAPI app entry point
├── config.py                   # Settings (DB URL, Redis URL, cache TTL)
├── database.py                 # SQLAlchemy engine + session factory
├── models/
│   ├── db_models.py            # SQLAlchemy ORM models
│   └── api_models.py           # Pydantic request/response schemas
├── core/
│   ├── profile_loader.py       # Fetches + caches insurer profiles
│   ├── dimension_resolver.py   # Converts raw inputs → canonical dims
│   ├── cluster_resolver.py     # RTO → cluster lookup
│   ├── segment_resolver.py     # CC/GVW/category → segment key
│   ├── rule_match_engine.py    # Exact + partial rule matching
│   ├── deduction_engine.py     # CPA, ZD, and special-rule deductions
│   └── result_aggregator.py    # Multi-insurer result collation
├── routers/
│   ├── commission.py           # Commission query endpoints
│   └── vehicle_meta.py         # Make/model/RTO lookup endpoints
└── utils/
    ├── reg_no_parser.py        # Reg no → RTO code
    └── cache.py                # Redis wrapper
```

### 8.2 `core/profile_loader.py`

```python
"""
profile_loader.py
Fetches the latest insurer schema profile from the DB.
Caches for PROFILE_CACHE_TTL seconds using Redis.
"""

from __future__ import annotations
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from config import settings
from utils.cache import redis_client

logger = logging.getLogger(__name__)
PROFILE_CACHE_TTL = 300  # 5 minutes


class ProfileNotFoundError(Exception):
    """Raised when no active profile exists for the given insurer."""


def load_profile(insurer_id: str, db: Session) -> dict[str, Any]:
    """
    Return the latest extraction_config dict for the given insurer_id.

    Resolution order:
      1. Redis cache  (key: profile:{insurer_id})
      2. PostgreSQL   insurer_schema_profiles (latest version)

    Args:
        insurer_id: Short code, e.g. "digisafe" or "chola".
        db:         Active SQLAlchemy session.

    Returns:
        The extraction_config JSONB as a Python dict.

    Raises:
        ProfileNotFoundError: If no profile row exists for this insurer.
    """
    cache_key = f"profile:{insurer_id}"

    # Try cache first
    cached = redis_client.get(cache_key)
    if cached:
        logger.debug("Profile cache hit for %s", insurer_id)
        return json.loads(cached)

    # Load from DB — latest version row
    row = db.execute(
        """
        SELECT isp.extraction_config
        FROM insurer_schema_profiles isp
        JOIN insurers i ON i.id = isp.insurer_id
        WHERE i.short_code = :insurer_id
        ORDER BY isp.profile_version DESC
        LIMIT 1
        """,
        {"insurer_id": insurer_id},
    ).fetchone()

    if not row:
        raise ProfileNotFoundError(f"No schema profile found for insurer: {insurer_id}")

    profile: dict[str, Any] = row.extraction_config

    # Warm cache
    redis_client.setex(cache_key, PROFILE_CACHE_TTL, json.dumps(profile))
    logger.info("Profile loaded and cached for %s", insurer_id)
    return profile


def flush_insurer_cache(insurer_id: str) -> None:
    """Remove all cached data for the given insurer (called on grid publish)."""
    keys = redis_client.keys(f"*:{insurer_id}:*") + [f"profile:{insurer_id}"]
    if keys:
        redis_client.delete(*keys)
    logger.info("Cache flushed for insurer: %s", insurer_id)
```

### 8.3 `core/dimension_resolver.py`

```python
"""
dimension_resolver.py
Converts raw user-submitted form values into canonical dimension predicates
ready to be matched against rule_dimensions in the DB.
"""

from __future__ import annotations
import re
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from core.cluster_resolver import resolve_cluster
from core.segment_resolver import resolve_segment
from utils.reg_no_parser import parse_reg_no


def resolve_dimensions(
    form: dict[str, Any],
    profile: dict[str, Any],
    db: Session,
    insurer_id: str,
) -> dict[str, Any]:
    """
    Converts raw form submission into a canonical dimension dict.

    Args:
        form:        Raw form values (vehicle_category, cc, fuel_type, etc.)
        profile:     The insurer's extraction_config dict.
        db:          SQLAlchemy session (for RTO lookups).
        insurer_id:  Short code of the insurer being queried.

    Returns:
        Dict of {dimension_key: canonical_value} for use in rule matching.
        Also includes derived metadata (rto_code, age_years, sub_product).
    """
    dims: dict[str, Any] = {}
    meta: dict[str, Any] = {}

    # ── 1. Issue date / vehicle age ──────────────────────────────────────
    issue_date = form.get("issue_date") or date.today()
    mfg_date   = form["vehicle"]["mfg_date"]    # "YYYY-MM"
    mfg_year, mfg_month = map(int, mfg_date.split("-"))
    age_years = (
        issue_date.year - mfg_year
        - (1 if issue_date.month < mfg_month else 0)
    )
    meta["vehicle_age_years"] = age_years

    # Use the profile's query_dimensions to know the canonical key name
    qd = profile.get("query_dimensions", {})
    age_key = qd.get("age_field", "age")
    dims[age_key] = age_years

    # ── 2. RTO / cluster ──────────────────────────────────────────────────
    reg_no = form["vehicle"].get("reg_no")
    if reg_no:
        rto_code = parse_reg_no(reg_no)
    else:
        rto_code = form["vehicle"].get("state_code")   # fallback: state selector

    meta["rto_code"] = rto_code
    cluster = resolve_cluster(rto_code, insurer_id, profile, db)
    meta["cluster_used"] = cluster

    cluster_key = qd.get("cluster_field", "cluster")
    if cluster:
        dims[cluster_key] = cluster

    # ── 3. Segment ────────────────────────────────────────────────────────
    segment = resolve_segment(
        vehicle_category=form["vehicle"]["category"],
        cc=form["vehicle"].get("cc"),
        kw=form["vehicle"].get("kw"),
        gvw_kg=form["vehicle"].get("gvw_kg"),
        make=form["vehicle"].get("make", ""),
        fuel_type=form["vehicle"].get("fuel_type", ""),
        profile=profile,
    )
    meta["segment"] = segment
    segment_key = qd.get("vehicle_category_field", "segment")
    if segment:
        dims[segment_key] = segment

    # ── 4. Fuel type ──────────────────────────────────────────────────────
    fuel_raw = form["vehicle"].get("fuel_type", "").lower()
    # Look up alias in profile
    fuel_aliases = (
        profile.get("dimension_aliases", {}).get("pc_fuel_type", {})
        or profile.get("dimension_aliases", {}).get("tw_fuel_type", {})
    )
    fuel_dim = fuel_aliases.get(fuel_raw, fuel_raw)   # passthrough if no alias
    fuel_key = qd.get("fuel_type_field", "fuel_type")
    dims[fuel_key] = fuel_dim

    # ── 5. Policy type (from coverage) ───────────────────────────────────
    coverage  = form["policy"]["coverage"]           # OD / TP / COMP
    pt_map    = profile.get("coverage_to_policy_type", {})
    pt_values = pt_map.get(coverage, [coverage.lower()])
    policy_type_key = qd.get("coverage_field", "policy_type")
    dims[policy_type_key] = pt_values[0]             # primary value; expand if needed

    # ── 6. NCB ────────────────────────────────────────────────────────────
    ncb_pct    = int(form["policy"].get("ncb_pct", 0))
    case_type  = form["policy"].get("case_type", "NEW")
    if case_type in ("NEW", "ROLLOVER", "BREAKIN"):
        ncb_pct = 0   # force to 0 for non-renewal cases

    ncb_map    = profile.get("ncb_dimension_map", {})
    ncb_values = ncb_map.get("values", {})
    ncb_dim    = ncb_values.get(str(ncb_pct), "without_NCB")
    ncb_key    = qd.get("ncb_field", "ncb_flag")
    dims[ncb_key] = ncb_dim
    meta["ncb_pct"] = ncb_pct

    # ── 7. Sub-product ────────────────────────────────────────────────────
    sub_product = _derive_sub_product(
        vehicle_category=form["vehicle"]["category"],
        coverage=coverage,
        age_years=age_years,
        case_type=case_type,
        policy_term=form["policy"].get("policy_term", "1+1"),
        profile=profile,
    )
    meta["sub_product"] = sub_product

    return {"dimensions": dims, "meta": meta}


def _derive_sub_product(
    vehicle_category: str,
    coverage: str,
    age_years: int,
    case_type: str,
    policy_term: str,
    profile: dict,
) -> str:
    """Maps vehicle_category + coverage + policy_term → sub_product key."""
    cat_map = profile.get("vehicle_category_to_segment", {})
    entry   = cat_map.get(vehicle_category, {})
    sheet   = entry.get("use_sheet")
    if sheet:
        # Map sheet → sub_product via sub_product_map in profile
        return profile.get("sub_product_map", {}).get(sheet, vehicle_category.lower())

    # Standard fallbacks
    if "2W" in vehicle_category:
        if coverage == "OD":
            return "tw_saod"
        if policy_term == "1+5":
            return "tw_1plus5"
        if policy_term == "5+5":
            return "tw_5plus5"
        return "tw_new_comp_satp"
    if vehicle_category == "PC":
        return "pc_tp_only" if coverage == "TP" else "pc_comp_saod"
    if vehicle_category == "GCV_HEAVY":
        return "gcv_heavy"
    return "gcv_light"
```

### 8.4 `core/rule_match_engine.py`

```python
"""
rule_match_engine.py
Queries commission_rules + rule_dimensions for an exact or partial match.
"""

from __future__ import annotations
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Dimension relaxation order: least specific → most specific
RELAXATION_ORDER = [
    "ncb_flag",
    "fuel_type",
    "age",
    "make",
    "cluster",
]


def find_best_rule(
    grid_id: str,
    sub_product: str,
    dimensions: dict[str, str],
    db: Session,
) -> dict[str, Any] | None:
    """
    Attempt exact match, then progressively relax dimensions.

    Returns:
        Dict with keys: rule, match_type, dimensions_relaxed, resolution_ms.
        None if no match found even after full relaxation.
    """
    t0 = time.monotonic()

    # Exact match attempt
    rule = _query_rules(grid_id, sub_product, dimensions, db)
    if rule:
        return {
            "rule": rule,
            "match_type": "EXACT",
            "dimensions_relaxed": [],
            "resolution_ms": int((time.monotonic() - t0) * 1000),
        }

    # Partial match — relax dimensions one at a time
    relaxed = []
    working_dims = dict(dimensions)

    for dim_key in RELAXATION_ORDER:
        if dim_key not in working_dims:
            continue
        removed_value = working_dims.pop(dim_key)
        relaxed.append(dim_key)
        logger.debug("Relaxing dimension: %s (was %s)", dim_key, removed_value)

        rule = _query_rules(grid_id, sub_product, working_dims, db)
        if rule:
            return {
                "rule": rule,
                "match_type": "PARTIAL",
                "dimensions_relaxed": relaxed,
                "resolution_ms": int((time.monotonic() - t0) * 1000),
            }

    return None   # NO_MATCH


def _query_rules(
    grid_id: str,
    sub_product: str,
    dimensions: dict[str, str],
    db: Session,
) -> dict | None:
    """
    Core SQL query: find commission rules that match ALL given dimensions.
    Uses a HAVING COUNT approach to ensure all predicates are satisfied.
    Returns the highest-specificity rule.
    """
    if not dimensions:
        return None

    # Build parameterised CASE conditions
    cases = " + ".join(
        f"CASE WHEN rd.dimension_key = :key_{i} "
        f"AND rd.dimension_value = :val_{i} THEN 1 ELSE 0 END"
        for i in range(len(dimensions))
    )

    params: dict[str, Any] = {
        "grid_id":     grid_id,
        "sub_product": sub_product,
        "n_dims":      len(dimensions),
    }
    for i, (k, v) in enumerate(dimensions.items()):
        params[f"key_{i}"] = k
        params[f"val_{i}"] = str(v)

    sql = f"""
        SELECT
            cr.id,
            cr.rule_name,
            cr.commission_rate,
            cr.od_commission_rate,
            cr.tp_commission_rate,
            cr.reward_percent,
            cr.payout_basis,
            cr.match_specificity
        FROM commission_rules cr
        JOIN rule_dimensions rd ON rd.rule_id = cr.id
        WHERE cr.grid_id   = :grid_id
          AND cr.sub_product = :sub_product
          AND cr.is_active = TRUE
        GROUP BY cr.id
        HAVING SUM({cases}) = :n_dims
        ORDER BY cr.match_specificity DESC
        LIMIT 1
    """

    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None
```

### 8.5 `routers/commission.py`

```python
"""
commission.py  —  FastAPI router for commission query endpoints.
"""

from __future__ import annotations
from datetime import date
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.api_models import CommissionQueryRequest, CommissionQueryResponse
from core.profile_loader import load_profile, ProfileNotFoundError
from core.dimension_resolver import resolve_dimensions
from core.rule_match_engine import find_best_rule
from core.deduction_engine import apply_deductions

router = APIRouter(prefix="/api/v1/commission", tags=["commission"])


@router.post("/query", response_model=CommissionQueryResponse)
def query_commission(
    body: CommissionQueryRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Main commission lookup endpoint.
    Accepts vehicle + policy details, returns commission rates per insurer.
    """
    session_id = str(uuid.uuid4())
    results    = []

    insurer_ids = _resolve_insurer_ids(body, db)

    for insurer_id in insurer_ids:
        try:
            profile = load_profile(insurer_id, db)
        except ProfileNotFoundError:
            continue

        # Resolve product_line and find active grid
        product_line = _vehicle_to_product_line(body.vehicle.category, profile)
        grid = _get_active_grid(insurer_id, product_line, db)
        if not grid:
            continue

        # Resolve dimensions from form inputs
        resolved = resolve_dimensions(
            form=body.model_dump(),
            profile=profile,
            db=db,
            insurer_id=insurer_id,
        )

        # Match rules
        match = find_best_rule(
            grid_id=grid["id"],
            sub_product=resolved["meta"]["sub_product"],
            dimensions=resolved["dimensions"],
            db=db,
        )

        if not match:
            _log_lookup(session_id, insurer_id, grid, resolved, None, "NO_MATCH", [], db)
            continue

        rule = match["rule"]

        # Apply deductions (CPA, special rules)
        final_rate, deductions = apply_deductions(
            base_rate=float(rule["commission_rate"]),
            profile=profile,
            cpa_collected=body.policy.cpa_collected,
            sub_product=resolved["meta"]["sub_product"],
        )

        # Fetch relevant grid notes
        notes = _get_grid_notes(grid["id"], db)

        result = {
            "insurer_id":           insurer_id,
            "insurer_name":         grid["insurer_name"],
            "commission_rate":      float(rule["commission_rate"]),
            "od_commission_rate":   rule.get("od_commission_rate"),
            "tp_commission_rate":   rule.get("tp_commission_rate"),
            "final_rate":           final_rate,
            "deductions_applied":   deductions,
            "payout_basis":         rule.get("payout_basis"),
            "match_type":           match["match_type"],
            "dimensions_relaxed":   match["dimensions_relaxed"],
            "rule_id":              rule["id"],
            "cluster_used":         resolved["meta"].get("cluster_used"),
            "notes":                [n["note_text"] for n in notes],
        }
        results.append(result)
        _log_lookup(session_id, insurer_id, grid, resolved, rule, match["match_type"],
                    match["dimensions_relaxed"], db)

    best = max(results, key=lambda r: r["final_rate"]) if results else None

    return {
        "session_id":   session_id,
        "queried_at":   date.today().isoformat(),
        "resolved":     results[0]["cluster_used"] if results else None,
        "results":      results,
        "best_result":  {"insurer_id": best["insurer_id"],
                         "commission_rate": best["final_rate"]} if best else None,
    }


def _resolve_insurer_ids(body: CommissionQueryRequest, db: Session) -> list[str]:
    """Return list of insurer short_codes to query."""
    if body.best_of_all:
        rows = db.execute(
            "SELECT short_code FROM insurers WHERE is_active = TRUE"
        ).fetchall()
        return [r.short_code for r in rows]
    return body.insurer_ids


def _vehicle_to_product_line(vehicle_category: str, profile: dict) -> str:
    cat_map = profile.get("vehicle_category_to_segment", {})
    entry   = cat_map.get(vehicle_category, {})
    return entry.get("product_line", "PC")


def _get_active_grid(insurer_id: str, product_line: str, db: Session) -> dict | None:
    row = db.execute(
        """
        SELECT cg.id, cg.grid_version, i.name AS insurer_name
        FROM commission_grids cg
        JOIN insurers i ON i.id = cg.insurer_id
        WHERE i.short_code = :iid
          AND cg.product_line = :pl
          AND cg.is_active = TRUE
        LIMIT 1
        """,
        {"iid": insurer_id, "pl": product_line},
    ).fetchone()
    return dict(row) if row else None


def _get_grid_notes(grid_id: str, db: Session) -> list[dict]:
    rows = db.execute(
        "SELECT note_text FROM grid_notes WHERE grid_id = :gid ORDER BY sort_order",
        {"gid": grid_id},
    ).fetchall()
    return [dict(r) for r in rows]


def _log_lookup(session_id, insurer_id, grid, resolved, rule, match_type, dims_relaxed, db):
    db.execute(
        """
        INSERT INTO lookup_logs
          (id, insurer_id, grid_id, rule_id, agent_id, product_line,
           query_params, rate_returned, match_type, dimensions_relaxed,
           resolution_ms, queried_at)
        VALUES
          (gen_random_uuid(), :iid, :gid, :rid, :aid, :pl,
           :qp, :rate, :mt, :dr, 0, NOW())
        """,
        {
            "iid":  insurer_id,
            "gid":  grid["id"] if grid else None,
            "rid":  rule["id"] if rule else None,
            "aid":  "system",
            "pl":   resolved["meta"].get("sub_product"),
            "qp":   str(resolved["dimensions"]),
            "rate": rule["commission_rate"] if rule else None,
            "mt":   match_type,
            "dr":   dims_relaxed,
        },
    )
    db.commit()
```

---

## 9. Code — Frontend (React)

### 9.1 Component tree

```
<CommissionApp>
  ├── <InsurerSelector>          # multi-select + "Best of All" checkbox
  ├── <VehicleSection>
  │     ├── <CategoryDropdown>   # triggers conditional field logic
  │     ├── <MakeModelSearch>    # cascading searchable dropdowns
  │     ├── <RegistrationInput>  # reg no → auto-fills RTO badge
  │     ├── <ManufacturingDate>  # month+year → shows "Age: X years"
  │     └── <ConditionalFields>  # CC / GVW / SeatingCapacity
  ├── <PolicySection>
  │     ├── <FuelTypeDropdown>
  │     ├── <CoverageRadio>      # OD / TP / COMP
  │     ├── <CaseTypeRadio>      # NEW / RENEWAL / ROLLOVER / BREAK-IN
  │     ├── <NCBRadio>           # 0 / 20 / 25 / 35 / 45 / 50
  │     └── <AddOnCheckboxes>    # CPA collected, Zero Dep
  └── <ResultsPanel>
        ├── <InsurerRateCard[]>  # one card per insurer result
        ├── <BestRateBadge>
        └── <AuditFooter>        # session_id, resolved RTO, match quality
```

### 9.2 Key React code

```jsx
// CommissionQueryForm.jsx

import { useState, useEffect, useCallback } from "react";
import { InsurerSelector } from "./InsurerSelector";
import { VehicleSection } from "./VehicleSection";
import { PolicySection } from "./PolicySection";
import { ResultsPanel } from "./ResultsPanel";

const VEHICLE_CATEGORIES = [
  { value: "2W_BIKE",    label: "2W – Bike" },
  { value: "2W_SCOOTER", label: "2W – Scooter" },
  { value: "PC",         label: "Private Car" },
  { value: "TAXI",       label: "Taxi / 3W" },
  { value: "SCHOOL_BUS", label: "School Bus" },
  { value: "STAFF_BUS",  label: "Staff Bus" },
  { value: "GCV_LIGHT",  label: "Goods Carrying – Light" },
  { value: "GCV_HEAVY",  label: "Goods Carrying – Heavy" },
  { value: "MISC",       label: "Miscellaneous" },
];

const NCB_OPTIONS = [0, 20, 25, 35, 45, 50];
const COVERAGE_OPTIONS = ["OD", "TP", "COMP"];
const CASE_TYPES = ["NEW", "RENEWAL", "ROLLOVER", "BREAKIN"];

export function CommissionQueryForm() {
  const today = new Date().toISOString().split("T")[0];

  const [form, setForm] = useState({
    issue_date:  today,
    insurer_ids: [],
    best_of_all: true,
    vehicle: {
      category: "",
      make: "",
      model: "",
      reg_no: "",
      mfg_date: "",
      cc: null,
      kw: null,
      fuel_type: "",
      gvw_kg: null,
      seating_capacity: null,
    },
    policy: {
      coverage: "COMP",
      case_type: "NEW",
      ncb_pct: 0,
      cpa_collected: true,
      zero_dep: false,
      policy_term: "1+1",
    },
  });

  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [rtoMeta, setRtoMeta] = useState(null);

  // Auto-resolve RTO when reg_no is fully entered
  useEffect(() => {
    const reg = form.vehicle.reg_no;
    if (reg && reg.length >= 8) {
      fetch(`/api/v1/commission/vehicle-meta?reg_no=${reg}`)
        .then(r => r.json())
        .then(data => setRtoMeta(data))
        .catch(() => setRtoMeta(null));
    }
  }, [form.vehicle.reg_no]);

  // Force NCB=0 for non-renewal case types
  useEffect(() => {
    if (["NEW", "ROLLOVER", "BREAKIN"].includes(form.policy.case_type)) {
      setForm(f => ({ ...f, policy: { ...f.policy, ncb_pct: 0 } }));
    }
  }, [form.policy.case_type]);

  const handleSubmit = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/commission/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      setResults(await res.json());
    } finally {
      setLoading(false);
    }
  }, [form]);

  // Which fields to show based on vehicle_category
  const showCC       = ["2W_BIKE", "2W_SCOOTER", "PC", "TAXI"].includes(form.vehicle.category);
  const showGVW      = ["GCV_LIGHT", "GCV_HEAVY"].includes(form.vehicle.category);
  const showSeating  = ["TAXI", "SCHOOL_BUS", "STAFF_BUS"].includes(form.vehicle.category);
  const ncbDisabled  = ["NEW", "ROLLOVER", "BREAKIN"].includes(form.policy.case_type);

  const setVehicle = (patch) =>
    setForm(f => ({ ...f, vehicle: { ...f.vehicle, ...patch } }));
  const setPolicy  = (patch) =>
    setForm(f => ({ ...f, policy:  { ...f.policy,  ...patch } }));

  return (
    <div className="commission-form">

      {/* ── Issue Date ── */}
      <label>Issue Date</label>
      <input
        type="date"
        value={form.issue_date}
        onChange={e => setForm(f => ({ ...f, issue_date: e.target.value }))}
      />

      {/* ── Insurer selector ── */}
      <InsurerSelector
        selected={form.insurer_ids}
        bestOfAll={form.best_of_all}
        onChange={(ids, best) =>
          setForm(f => ({ ...f, insurer_ids: ids, best_of_all: best }))
        }
      />

      {/* ── Vehicle Category ── */}
      <label>Vehicle Category *</label>
      <select
        value={form.vehicle.category}
        onChange={e => setVehicle({ category: e.target.value })}
      >
        <option value="">— Select —</option>
        {VEHICLE_CATEGORIES.map(c => (
          <option key={c.value} value={c.value}>{c.label}</option>
        ))}
      </select>

      {/* ── Make / Model ── */}
      <MakeModelSearch
        category={form.vehicle.category}
        make={form.vehicle.make}
        model={form.vehicle.model}
        onChange={(make, model, meta) => setVehicle({
          make,
          model,
          cc: meta?.cc_min ?? form.vehicle.cc,
          fuel_type: meta?.fuel_types?.[0] ?? form.vehicle.fuel_type,
          gvw_kg: meta?.gvw_kg ?? form.vehicle.gvw_kg,
          seating_capacity: meta?.seating_capacity ?? form.vehicle.seating_capacity,
        })}
      />

      {/* ── Registration No ── */}
      <label>Registration No (optional)</label>
      <input
        type="text"
        placeholder="e.g. KA05AB1234"
        value={form.vehicle.reg_no}
        onChange={e => setVehicle({ reg_no: e.target.value.toUpperCase() })}
      />
      {rtoMeta && (
        <span className="rto-badge">
          RTO: {rtoMeta.rto_code} — {rtoMeta.rto_name} ({rtoMeta.state_code})
        </span>
      )}

      {/* ── Manufacturing Date ── */}
      <label>Manufacturing Date *</label>
      <input
        type="month"
        value={form.vehicle.mfg_date}
        onChange={e => setVehicle({ mfg_date: e.target.value })}
      />
      {form.vehicle.mfg_date && (
        <span>Age: {computeAge(form.vehicle.mfg_date)} year(s)</span>
      )}

      {/* ── Conditional numeric fields ── */}
      {showCC && (
        <>
          <label>{form.vehicle.fuel_type === "electric" ? "Motor Power (KW)" : "Engine CC"}</label>
          <input
            type="number"
            value={form.vehicle.fuel_type === "electric" ? form.vehicle.kw : form.vehicle.cc}
            onChange={e =>
              form.vehicle.fuel_type === "electric"
                ? setVehicle({ kw: Number(e.target.value) })
                : setVehicle({ cc: Number(e.target.value) })
            }
          />
        </>
      )}
      {showGVW && (
        <>
          <label>Gross Vehicle Weight (kg)</label>
          <input type="number" value={form.vehicle.gvw_kg ?? ""}
            onChange={e => setVehicle({ gvw_kg: Number(e.target.value) })} />
        </>
      )}
      {showSeating && (
        <>
          <label>Seating Capacity</label>
          <input type="number" value={form.vehicle.seating_capacity ?? ""}
            onChange={e => setVehicle({ seating_capacity: Number(e.target.value) })} />
        </>
      )}

      {/* ── Fuel Type ── */}
      <label>Fuel Type *</label>
      <select value={form.vehicle.fuel_type}
        onChange={e => setVehicle({ fuel_type: e.target.value })}>
        <option value="">— Select —</option>
        {["Petrol", "Diesel", "CNG", "Electric", "Hybrid"].map(f => (
          <option key={f} value={f.toLowerCase()}>{f}</option>
        ))}
      </select>

      {/* ── Coverage ── */}
      <label>Coverage *</label>
      <div className="radio-group">
        {COVERAGE_OPTIONS.map(c => (
          <label key={c}>
            <input type="radio" name="coverage" value={c}
              checked={form.policy.coverage === c}
              onChange={() => setPolicy({ coverage: c })} />
            {c}
          </label>
        ))}
      </div>

      {/* ── Case Type ── */}
      <label>Case Type *</label>
      <div className="radio-group">
        {CASE_TYPES.map(t => (
          <label key={t}>
            <input type="radio" name="case_type" value={t}
              checked={form.policy.case_type === t}
              onChange={() => setPolicy({ case_type: t })} />
            {t}
          </label>
        ))}
      </div>

      {/* ── NCB % ── */}
      <label>NCB %</label>
      <div className="radio-group">
        {NCB_OPTIONS.map(n => (
          <label key={n} style={{ opacity: ncbDisabled ? 0.4 : 1 }}>
            <input type="radio" name="ncb" value={n}
              checked={form.policy.ncb_pct === n}
              disabled={ncbDisabled}
              onChange={() => setPolicy({ ncb_pct: n })} />
            {n}%
          </label>
        ))}
      </div>

      {/* ── Add-ons ── */}
      <label>
        <input type="checkbox" checked={form.policy.cpa_collected}
          onChange={e => setPolicy({ cpa_collected: e.target.checked })} />
        CPA Premium Collected
      </label>
      <label>
        <input type="checkbox" checked={form.policy.zero_dep}
          onChange={e => setPolicy({ zero_dep: e.target.checked })} />
        Zero Depreciation Add-on
      </label>

      {/* ── Submit ── */}
      <button onClick={handleSubmit} disabled={loading}>
        {loading ? "Fetching Rates…" : "Get Commission Rates"}
      </button>

      {results && <ResultsPanel data={results} />}
    </div>
  );
}

function computeAge(mfgDate) {
  const [y, m] = mfgDate.split("-").map(Number);
  const now = new Date();
  return now.getFullYear() - y - (now.getMonth() + 1 < m ? 1 : 0);
}
```

---

## 10. RTO → Cluster Mapping

### 10.1 How the mapping table is populated

The `cluster_rto_mappings` table is populated during ingestion, not manually. The process:

```
1. Ingestor reads profile.cluster_geography_map (or cv_rto_cluster_map for CV light)
2. For each cluster_name → [state_codes or rto_codes]:
   - If entry is a state code (2 chars, e.g. "MH"):
       INSERT INTO cluster_rto_mappings (insurer_id, cluster_name, state_code)
   - If entry is a specific RTO (e.g. "KA-BLR"):
       Resolve to rto_code via rto_master, then insert with rto_code
3. Quality cohort (good/bad/ref) is extracted from cluster_name suffix
```

### 10.2 RTO code parser

```python
# utils/reg_no_parser.py

import re

RTO_PATTERN = re.compile(r"^([A-Z]{2})(\d{2})[A-Z]{1,2}\d{4}$")

def parse_reg_no(reg_no: str) -> str | None:
    """
    Extract RTO code from a vehicle registration number.
    
    Examples:
        KA05AB1234  →  KA05
        MH01AA1234  →  MH01
        DL3CAB1234  →  DL3C  (old format)

    Returns:
        RTO code string, or None if format unrecognised.
    """
    reg = reg_no.strip().upper().replace(" ", "").replace("-", "")
    m = RTO_PATTERN.match(reg)
    if m:
        return m.group(1) + m.group(2)   # state + district number
    # Fallback: return first 4 characters as RTO code
    if len(reg) >= 4 and reg[:2].isalpha():
        return reg[:4]
    return None
```

---

## 11. Edge Cases and Special Rules

| Scenario | How It's Handled |
|---|---|
| **HCV TATA Age 0 — two rate variants** | `rule_match_engine` creates two `commission_rules` rows during import: one with `addon_flags=['with_addon']`, one with `['without_addon']`. Query sends the relevant flag based on policy add-on selection. |
| **MISP track vehicles** | `commission_rate = NULL`, `payout_basis = 'misp_od_addon'`. API response shows rate as `null` with a note "MISP pricing applies — contact OEM desk". |
| **Staff Bus ≤ 10 seater** | `segment_resolver` checks seating_capacity ≤ 10 and sets `override_cd1_pct=85, override_cd2_pct=20` in resolved_dims. `deduction_engine` applies override before returning rate. |
| **Short-term CV policy** | `case_type = SHORT_TERM` (add to enum). `deduction_engine` reads `cv_short_term_rate_derivation` special rule and applies percentage reduction to the annual rate. |
| **Chola NOP slab (TW)** | NOP is not a vehicle attribute — it's a portfolio dimension. Add `nop_slab` as an optional form field that appears only for TW Chola queries when the agent has volume data. |
| **DigiSafe Non-Motor** | `product_line = NON_MOTOR`. The form shows completely different fields (SI amount, risk type, WC monthly premium). Implement as a separate `NonMotorQueryForm` component that shares the same API endpoint. |
| **CPA deduction** | `deduction_engine` checks `profile.addon_deductions.CPA_not_collected`. If `cpa_collected = false` AND sub_product is in `applies_to`: `final_rate = commission_rate - 1.5`. |
| **Break-in / Rollover** | `ncb_pct` forced to 0. `case_type_rules` sets `flag: needs_review` — response includes `"review_required": true`. |
| **No RTO match (cluster unknown)** | Cluster dimension is omitted from predicates. Engine tries without cluster (partial match). `dimensions_relaxed` includes `"cluster"`. |
| **Multiple clusters for one RTO** | When `cluster_rto_mappings` returns Good + Bad rows for same RTO: API returns both rates and lets agent choose cohort (or default to Good). |

---

## 12. Testing Strategy

### 12.1 Unit tests

```python
# tests/test_dimension_resolver.py

def test_ncb_forced_zero_for_new_business():
    form = {"policy": {"ncb_pct": 25, "case_type": "NEW"}, ...}
    result = resolve_dimensions(form, profile=DIGISAFE_PROFILE, ...)
    assert result["dimensions"]["ncb_flag"] == "without_NCB"

def test_age_computation_cross_year():
    # Vehicle manufactured Feb 2021, query in Jan 2026
    form = {"vehicle": {"mfg_date": "2021-02"}, "issue_date": "2026-01-15", ...}
    result = resolve_dimensions(form, ...)
    assert result["meta"]["vehicle_age_years"] == 4   # NOT 5

def test_cluster_resolution_rto_exact():
    # KA05 → Rest_of_KA_Good for DigiSafe
    cluster = resolve_cluster("KA05", "digisafe", DIGISAFE_PROFILE, db)
    assert cluster == "Rest_of_KA_Good"

def test_segment_pc_cc_1200():
    segment = resolve_segment(
        vehicle_category="PC", cc=1200, fuel_type="petrol", profile=DIGISAFE_PROFILE
    )
    assert segment == "petrol_1000_1500cc"
```

### 12.2 Integration tests

```python
# tests/test_commission_query.py

def test_exact_match_digisafe_pc_comp():
    resp = client.post("/api/v1/commission/query", json={
        "insurer_ids": ["digisafe"],
        "vehicle": {"category": "PC", "mfg_date": "2022-03", "cc": 1197,
                    "fuel_type": "petrol", "reg_no": "KA05AB1234"},
        "policy":  {"coverage": "COMP", "case_type": "RENEWAL", "ncb_pct": 25,
                    "cpa_collected": True},
    })
    data = resp.json()
    assert data["results"][0]["match_type"] == "EXACT"
    assert data["results"][0]["commission_rate"] > 0

def test_no_match_returns_empty_results():
    # Query for a segment known to be declined in DigiSafe
    resp = client.post("/api/v1/commission/query", json={
        "insurer_ids": ["digisafe"],
        "vehicle": {"category": "STAFF_BUS", "fuel_type": "electric", ...},
        "policy":  {"coverage": "COMP", "case_type": "NEW"},
    })
    assert resp.json()["results"] == []
```

### 12.3 Test data seeding

Create a `conftest.py` fixture that:
1. Loads the DigiSafe and Chola profiles into `insurer_schema_profiles`
2. Creates a sample `commission_grids` row
3. Inserts 10–15 representative `commission_rules` rows covering known combinations
4. Populates `cluster_rto_mappings` for KA, MH, DL

---

## 13. Deployment Notes

### 13.1 Environment variables

```env
DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/commission_db
REDIS_URL=redis://redis:6379/0
PROFILE_CACHE_TTL=300
ANTHROPIC_API_KEY=sk-ant-...   # for ingestion pipeline only
LOG_LEVEL=INFO
```

### 13.2 Required indexes (additions to existing schema)

```sql
-- Fast cluster resolution
CREATE INDEX idx_crm_insurer_rto
  ON cluster_rto_mappings(insurer_id, rto_code)
  WHERE rto_code IS NOT NULL;

-- Fast sub_product + grid_id filtering in rule matching
CREATE INDEX idx_cr_grid_subproduct
  ON commission_rules(grid_id, sub_product)
  WHERE is_active = TRUE;

-- Fast dimension key filtering
CREATE INDEX idx_rd_key_value
  ON rule_dimensions(dimension_key, dimension_value);
```

### 13.3 Caching strategy

| Data | Cache Key | TTL |
|---|---|---|
| Insurer profile | `profile:{insurer_id}` | 5 min |
| Active grid ID | `grid:{insurer_id}:{product_line}` | 5 min |
| RTO → cluster map | `cluster:{insurer_id}:{rto_code}` | 1 hour |
| Make list | `makes:{category}` | 24 hours |
| Model list | `models:{make}:{category}` | 24 hours |

All cache keys are flushed for an insurer when `publish_grid()` is called.

### 13.4 requirements.txt additions

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.9
redis>=5.0.0
pydantic>=2.7.0
python-dateutil>=2.9.0
```

---

*Document maintained by the Broker Tech Platform team. Update whenever a new insurer profile is onboarded or the query engine logic changes.*
