"""
DigiSafe Motor Insurance Commission Schema Profile
===================================================
Insurer : DigiSafe (Go Digit General Insurance)
File    : DigiSafe_Mar_26.ods
Period  : March 2026

Structural analysis of the ODS file revealed 12 sheets covering:
  - Motor (2W, 4W, CV/HCV) commission grids
  - Non-Motor (Property, Engineering, WC, Marine, Health) commission grids
  - Special-product grids (School Bus, Staff Bus, CV Short-Term)

This profile is the single source of truth for how the ingestion pipeline
must parse DigiSafe files. It follows the same schema contract as the
Chola MS profile so the same pipeline machinery can drive both.
"""

DIGISAFE_SCHEMA_PROFILE = {

    # ─────────────────────────────────────────────────────────────────────────
    # IDENTITY
    # ─────────────────────────────────────────────────────────────────────────
    "insurer_id": "digisafe",
    "short_name": "DigiSafe",
    "full_name": "Go Digit General Insurance Ltd.",

    # ─────────────────────────────────────────────────────────────────────────
    # FILE STRUCTURE
    # ─────────────────────────────────────────────────────────────────────────
    "file_structure": {
        # DigiSafe sends a single ODS file (OpenDocument Spreadsheet).
        # openpyxl cannot open ODS — use pandas with engine="odf".
        "type": "single_file_multi_sheet",
        "file_format": "ods",                  # NOT xlsx — critical for parser routing
        "parser_engine": "odf",               # pandas engine kwarg

        "active_sheets": {
            # Motor – Commercial Vehicle (light CV, 3W, tractors, e-vehicles)
            "CV": "cv_light",
            # Motor – Heavy Commercial Vehicle (12T+ trucks)
            "HCV": "cv_heavy",
            # Motor – 2W new business (1+1 comprehensive + SATP)
            "New 2W Comp & SATP": "tw_new_comp_satp",
            # Motor – 2W long-term (1+5 comprehensive)
            "2W Grid 1+5": "tw_1plus5",
            # Motor – 2W long-term (5+5 / bundled)
            "2W Grid 5+5": "tw_5plus5",
            # Motor – 2W stand-alone OD (SAOD)
            "2W SAOD": "tw_saod",
            # Motor – 4W stand-alone TP (third-party only)
            "4W TP": "pc_tp",
            # Motor – 4W comprehensive / SAOD (new business)
            "New 4W Comp_SAOD": "pc_comp_saod",
            # Non-Motor – Property, Engineering, WC, Marine, Health (multi-class single sheet)
            "Non-Motor Grid": "non_motor",
            # Special – School Bus prime-broker rates
            "School Bus - Prime Brokers": "school_bus",
            # Special – Staff Bus rates by state/RTO/usage-type
            "Staff Bus Grid": "staff_bus",
            # Reference – Short-term CV rate-reduction rules
            "CV Short term Policy grid": "cv_short_term",
        },

        # No delta/overlay sheets in this file (unlike Chola which has a 'New changes' sheet)
        "delta_sheets": [],

        # Sheets that contain narrative notes only (no rate data)
        "notes_sheets": [],

        # No sheets to ignore (all 12 are data-bearing)
        "ignore_sheets": [],

        # Pattern to match active motor sheets in future months' files
        "sheet_name_pattern": r"(CV|HCV|2W|4W|Non-Motor|School Bus|Staff Bus)",
    },

    # ─────────────────────────────────────────────────────────────────────────
    # RATE ENCODING
    # ─────────────────────────────────────────────────────────────────────────
    "rate_encoding": {
        # Rates are stored as decimals (0.25 = 25 %).
        # Multiply by 100 to convert to percentage form before DB insert.
        "format": "decimal",
        "multiply_by": 100,

        # Declined / no-payout markers
        "declined_markers": ["D", 0.0],          # "D" (string) = Declined, 0.0 = no commission
        "negative_cd1_meaning": "floor_adjustment",  # Negative CD1 values = downward adjustment on OD base

        # Special rate tokens that are NOT numeric commission rates
        "non_rate_tokens": {
            "MISP": "manufacturer_incentive_scheme_price",   # OEM/MISP pricing track — rate TBD externally
            "D":    "declined",
            "TBD":  "to_be_decided",                         # Appears in Non-Motor Engineering grid
        },

        # CD1 = target loss ratio / discount ceiling passed to insurer
        # CD2 = broker commission percentage (what this system stores as commission_rate)
        "rate_columns": {
            "cd1": "CD1",           # Discount to insurer / loss-ratio cap
            "cd2_comp": "CD2",      # Commission rate — Comprehensive / OD+TP
            "cd2_tp": "CD2",        # Commission rate — TP only (same column label, different sheet context)
        },

        # HCV sheet only: CD1 is expressed as a percentage of the OD premium ceiling
        # e.g. CD1 = 0.85 means broker must achieve ≤ 85 % loss ratio to earn the CD2
        "hcv_cd1_basis": "loss_ratio_ceiling",

        # Staff Bus grid: rates expressed as combined CD1/CD2 string e.g. "CD1 95% / CD2 40%"
        # Parse pattern:  r"CD1\s*([\d.]+)%\s*/\s*CD2\s*([\d.]+)%"
        "staff_bus_rate_format": "composite_string",
        "staff_bus_rate_pattern": r"CD1\s*([\d.]+)%\s*/\s*CD2\s*([\d.]+)%",

        # 2W SAOD: negative Min CD1 values are downward adjustments (e.g. -0.4 means CD1 floor is -40%)
        "saod_negative_cd1_allowed": True,

        # 2W 5+5 sheet has explicit CD2 reduction columns (e.g. W/O Add on = -0.05)
        "tw_5plus5_reduction_columns": {
            "W/O Add on":       -0.05,
            "W/O CPA":          -0.05,
            "W/o Add on and CPA": -0.10,
        },

        # 4W Comp_SAOD header note: 1+3 standard grid uses 19.5% OD with 90:10 rule;
        # 1+1 is Slab OD+AddOn with 90:10 rule
        "pc_comp_standard_grid_rate": 0.195,     # 19.5% — applied when no cluster-specific rate
        "pc_comp_rule_note": "90:10 rule applies — 90% of commission on OD premium, 10% on TP premium",
    },

    # ─────────────────────────────────────────────────────────────────────────
    # PRODUCT LINES
    # ─────────────────────────────────────────────────────────────────────────
    "product_lines": ["TW", "PC", "CV", "NON_MOTOR"],

    "product_line_map": {
        "CV":                        "CV",
        "HCV":                       "CV",        # HCV is a sub-type of CV product line
        "New 2W Comp & SATP":        "TW",
        "2W Grid 1+5":               "TW",
        "2W Grid 5+5":               "TW",
        "2W SAOD":                   "TW",
        "4W TP":                     "PC",
        "New 4W Comp_SAOD":          "PC",
        "Non-Motor Grid":            "NON_MOTOR",
        "School Bus - Prime Brokers":"CV",         # PCV sub-segment
        "Staff Bus Grid":            "CV",         # PCV sub-segment
        "CV Short term Policy grid": "CV",         # reference / modifier sheet
    },

    # Sub-product classification within product lines
    "sub_product_map": {
        "CV":                        "gcv_light",        # GCV up to 12T + PCV3W + tractors + EV
        "HCV":                       "gcv_heavy",        # GCV 12T+ (Non-Dumper/Tipper)
        "New 2W Comp & SATP":        "tw_new_comp_satp", # 1+1 new business
        "2W Grid 1+5":               "tw_long_term_1p5", # 1+5 long term
        "2W Grid 5+5":               "tw_long_term_5p5", # 5+5 bundled
        "2W SAOD":                   "tw_saod",          # Stand-alone OD
        "4W TP":                     "pc_tp_only",       # Third-party only
        "New 4W Comp_SAOD":          "pc_comp_saod",     # Comprehensive / SAOD
        "Non-Motor Grid":            "non_motor_multi",  # Multi-class non-motor
        "School Bus - Prime Brokers":"pcv_school_bus",   # School bus (PCV ≥ 8 seater)
        "Staff Bus Grid":            "pcv_staff_bus",    # Staff bus
        "CV Short term Policy grid": "cv_short_term_ref",# Short-term rate reference
    },

    # ─────────────────────────────────────────────────────────────────────────
    # GRID LAYOUT — per sheet
    # ─────────────────────────────────────────────────────────────────────────
    # DigiSafe uses a TALL (row-per-rule) layout, unlike Chola's wide crosstab.
    # Each row IS a commission rule. No transposition needed.
    "grid_layout": {
        "orientation": "tall_row_per_rule",     # fundamental difference from Chola

        # Header rows before data begins (0-indexed row of column headers)
        "header_row_by_sheet": {
            "CV":                        2,      # Row 2 is the header
            "HCV":                       2,
            "New 2W Comp & SATP":        2,
            "2W Grid 1+5":               2,
            "2W Grid 5+5":               1,
            "2W SAOD":                   1,
            "4W TP":                     2,
            "New 4W Comp_SAOD":          3,      # Multi-row header: rows 0-3; data starts row 4
            "Non-Motor Grid":            "multi_section",  # Multiple grids stacked vertically in one sheet
            "School Bus - Prime Brokers":2,
            "Staff Bus Grid":            1,
            "CV Short term Policy grid": 1,
        },

        # Column skip: column 0 is always blank/NaN — skip it
        "skip_col_index": 0,

        # 4W Comp_SAOD has a compound multi-row header (rows 1–3) encoding:
        #   Row 1: policy_category (Non HEV Comp / Non HEV SAOD / HEV)
        #   Row 2: fuel_type     (Petrol/EV | Diesel/CNG | HEV)
        #   Row 3: ncb_flag      (with NCB | without NCB)
        # These must be parsed together to label each rate column.
        "pc_comp_saod_header_rows": [1, 2, 3],
        "pc_comp_saod_data_start_row": 4,

        # Non-Motor sheet contains multiple grids stacked in one sheet.
        # Each sub-grid starts with a bold section title row.
        "non_motor_section_anchors": {
            "Property Grid":           0,
            "Engineering Grid":        11,
            "Workmen's Compensation":  23,
            "Marine Cargo":            31,
            "Retail Health":           39,
        },

        # HCV sheet: row 0 is a global note, not a column header
        "hcv_global_note_row": 0,
        "hcv_header_row": 2,

        # 2W Grid 5+5: has extra columns (Remarks, CD2 Reductions) after the main rate columns
        "tw_5plus5_extra_cols": ["Remarks", "CD2 Reductions"],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # DIMENSIONS — per sheet/product
    # ─────────────────────────────────────────────────────────────────────────
    "dimensions_by_sheet": {
        "CV": [
            "rto_cluster",          # Geographic cluster (ROM1, ROM2, ROM3, Delhi, …)
            "segment",              # Vehicle segment (GCV3, GCV4 upto 1.6T, PCV3W, E-Rickshaw, …)
            "make",                 # Vehicle make (All, Oil Tanker, Mahindra & AL, …)
            "carrier_type",         # Carrier type (All — only one value in current file)
            "age_from",             # Vehicle age range — from year (int)
            "age_to",               # Vehicle age range — to year (int)
            "cd1_from",             # CD1 band — lower bound
            "cd1_to",               # CD1 band — upper bound
        ],
        "HCV": [
            "cluster",              # Geographic cluster
            "segment",              # GCV weight band (GCV4 12 to 20T, GCV4 20 to 40T, …)
            "sub_segment",          # Non-Dumper/Tipper (currently only value)
            "make",                 # All or specific OEM (TATA, Bharat Benz, …)
            "age_from",
            "age_to",
            # Note: HCV CD1 is a range value embedded in the cell (e.g. "With Addon: 85% / Without Addon: 60%")
            # These are stored as special_rate_variants, not simple numeric dimensions
        ],
        "New 2W Comp & SATP": [
            "cluster",              # Agency/PB cluster (Andaman, APTS_Bad, APTS_Good1, …)
            "segment",              # Vehicle segment (SC/EV, MC ≤ 180 Hero/Honda, MC_180-350_RE, MC>350, …)
        ],
        "2W Grid 1+5": [
            "cluster",              # Agency/PB cluster
            "make",                 # OEM make (HERO MOTOCORP, BAJAJ, HONDA, ROYAL ENFIELD, SUZUKI, Others)
            "segment",              # Displacement/category (All, 3-7 KW, MC ≤155, MC >155, SCOOTER, …)
            "formula_type",         # Payout formula (Slab Net 5, Slab OD+Add on)
        ],
        "2W Grid 5+5": [
            "cluster",
            "make",
            "segment",
            "formula_type",
        ],
        "2W SAOD": [
            "cluster",
            "segment",              # MC <155, MC>155, RE, SC, SC_EV
            # cd1_no_break_in and cd1_break_in are separate rate columns, not dimensions
        ],
        "4W TP": [
            "cluster",              # Geographic cluster (can include sub-clusters like AP TS_Good1)
            "segment",              # Fuel-cc band (Petrol<1000, Petrol>1000, Petrol 1000-1500, Diesel<1500, …)
            "age",                  # Age band (All, <10, >10) as string — not a numeric range here
        ],
        "New 4W Comp_SAOD": [
            "cluster",
            "policy_type",          # Non HEV Comp / Non HEV SAOD / HEV — from compound header
            "fuel_type",            # Petrol/EV | Diesel/CNG | HEV
            "ncb_flag",             # with NCB | without NCB
        ],
        "Non-Motor Grid": [
            # Varies per sub-section — see non_motor_dimensions_by_section below
        ],
        "School Bus - Prime Brokers": [
            "state_group",          # State-group (Gujarat/Punjab/Delhi NCR, Uttar Pradesh, …)
            "rto_cluster",          # RTO-level cluster (Gujarat, Punjab, Delhi NCR, UP Open, …)
            "seating_capacity",     # 8 & above (or sub-ranges like 8-60, >60)
            "operator_type",        # SB In Name of School / On Contract Transporter / On Contract Individual
        ],
        "Staff Bus Grid": [
            "state",
            "rto",
            "usage_type",           # Corporate Self-Usage / Contract Carriage Transporter / Contract Carriage Individual
            "seating_capacity",     # >10 seater flag
        ],
        "CV Short term Policy grid": [
            "segment",              # School Bus / Staff Bus / Other CV
            # rate_applicable is a formula string (percentage reduction), not a raw numeric rate
        ],
    },

    # Non-Motor sheet dimensions vary by sub-section
    "non_motor_dimensions_by_section": {
        "Property Grid": [
            "insurer_share_type",   # Digit 100% / Digit Leader / Digit Follower
            "si_slab",              # Sum insured slab (Upto 225 Cr, 225-1000 Cr+)
            "risk_type",            # Preferred / Non-Preferred
            "si_amount_band",       # Upto 300,000 / 300,000+ (for CD2 column split)
            "cover_type",           # All (except IAR & Mega) / IAR/Mega/BI
        ],
        "Engineering Grid": [
            "our_share_slab",       # Upto 225 Crs / > 225 Cr Upto 1000 Crs (single) / Multi / All Other
            "tsi_upto",             # Total Sum Insured ceiling (225 Cr, 1000 Cr, 2499 Cr, 2499 Cr+)
            "project_duration",     # PP Upto 60 Months etc. / Any
            "risk_type",            # Preferred / Non-Preferred
        ],
        "Workmen's Compensation": [
            "risk_type",            # All
            "cd1_band",             # 0-80% / 80%-95% / Above 95%
            "monthly_volume_band",  # 5-10L / 10L+ (for CD2 uplift)
        ],
        "Marine Cargo": [
            "risk_type",            # Preferred / Non-Preferred
            "monthly_volume_band",  # 0-200,000 / 200,000+ (for Specific CD2 uplift)
            "policy_sub_type",      # Specific CD2 / Open/STOP
        ],
        "Retail Health": [
            "plan_product",         # Plan name (Double Wallet, Infinity Wallet, …)
            "volume_si_band",       # Volume slab (0-10k, 10k-25K, …)
            "policy_type",          # Retail Health Fresh / Retail PA Fresh / STP Port / NSTP Port / 1st Renewal
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    # CLUSTER / REGION DEFINITIONS
    # ─────────────────────────────────────────────────────────────────────────
    # DigiSafe uses named clusters (Good/Bad quality buckets + geographic splits)
    # rather than Chola's state + region group structure.
    "cluster_quality_suffixes": {
        "_Good": "good_cohort",     # Better portfolio quality / lower loss ratio cluster
        "_Bad":  "bad_cohort",      # Worse portfolio quality / higher loss ratio cluster
        "_Ref":  "referral_cohort", # Referral / prior-approval required
    },

    "cluster_geography_map": {
        # 4W TP cluster → constituent states/RTOs (non-exhaustive; extend from full grid)
        "Andaman":          ["AN"],
        "AP TS_Bad":        ["AP", "TS"],
        "AP TS_Good":       ["AP", "TS"],
        "AP TS_Good1":      ["AP", "TS"],
        "AP TS_Good2":      ["AP", "TS"],
        "Assam_Good":       ["AS"],
        "Bangalore":        ["KA-BLR"],
        "CG_Good":          ["CG"],
        "Chd_Tricity":      ["CH", "PB-TRI", "HR-TRI"],
        "Delhi_NCR":        ["DL", "HR-NCR", "UP-NCR"],
        "Delhi":            ["DL"],
        "NCR":              ["HR-NCR", "UP-NCR"],
        "GJ_MH_Ref":        ["GJ", "MH"],
        "Goa":              ["GA"],
        "Guj_Bad":          ["GJ"],
        "Guj_Good":         ["GJ"],
        "HP_Bad":           ["HP"],
        "HP_Good":          ["HP"],
        "HR_Bad":           ["HR"],
        "HR_Good":          ["HR"],
        "JK_Bad":           ["JK"],
        "JK_Good":          ["JK"],
        "JH_Bad":           ["JH"],
        "JH_Good":          ["JH"],
        "Kerala":           ["KL"],
        "MH_Pune":          ["MH-Pune"],
        "MP_Bad":           ["MP"],
        "MP_Good":          ["MP"],
        "Mumbai":           ["MH-Mumbai"],
        "NE_Ref":           ["AS", "ML", "MN", "MZ", "NL", "TR", "AR", "SK"],
        "North_East":       ["AS", "ML", "MN", "MZ", "NL", "TR", "AR", "SK"],
        "North_Ref":        ["UP", "UK", "HP", "HR"],
        "Orissa_Good":      ["OD"],
        "PB_Bad":           ["PB"],
        "PB_Good":          ["PB"],
        "Rest_of_KA_Bad":   ["KA"],
        "Rest_of_KA_Good":  ["KA"],
        "RJ_Bad":           ["RJ"],
        "RJ_Good":          ["RJ"],
        "ROMG_Bad":         ["MH"],
        "ROMG_Good":        ["MH"],
        "South_Ref":        ["TN", "KL", "AP", "TS"],
        "TN_Good":          ["TN"],
        "UK_Bad":           ["UK"],
        "UK_Good":          ["UK"],
        "UP_Bad":           ["UP"],
        "UP_Good":          ["UP"],
        "UP_UK_WB_REF":     ["UP", "UK", "WB"],
        "WB_Bad":           ["WB"],
        "WB_Good":          ["WB"],
        "WB_Kolkata":       ["WB-Kolkata"],
        "Bihar_Bad":        ["BR"],
        "Bihar_Good":       ["BR"],
        "All_India_Decline":["*"],
    },

    # CV light sheet uses RTO Cluster labels (ROM1–ROM3, Delhi, etc.)
    "cv_rto_cluster_map": {
        "ROM1": ["Rest of Maharashtra zone 1"],
        "ROM2": ["Rest of Maharashtra zone 2"],
        "ROM3": ["Rest of Maharashtra zone 3"],
        "Delhi": ["DL"],
        # Extend from full grid scan
    },

    # ─────────────────────────────────────────────────────────────────────────
    # DIMENSION ALIASES & VALUE NORMALISATION
    # ─────────────────────────────────────────────────────────────────────────
    "dimension_aliases": {

        # 2W segment names → canonical keys
        "tw_segment": {
            "SC/EV":                    "scooter_ev",
            "MC <= 180 Hero/Honda":     "mc_lte180_hero_honda",
            "MC <= 180 Others":         "mc_lte180_others",
            "MC_180-350_RE":            "mc_180_350_re",
            "MC_180-350_HONDA/JAWA/Avenger": "mc_180_350_honda_jawa_avenger",
            "MC_180-350_Others":        "mc_180_350_others",
            "MC_180-350_Other than RE": "mc_180_350_other_than_re",
            "MC>350":                   "mc_gt350",
            "MC <155":                  "mc_lt155",
            "MC>155":                   "mc_gt155",
            "RE":                       "royal_enfield",
            "SC":                       "scooter",
            "SC_EV":                    "scooter_ev",
        },

        # 2W make names → canonical keys
        "tw_make": {
            "HERO MOTOCORP":            "hero_motocorp",
            "BAJAJ":                    "bajaj",
            "HONDA":                    "honda",
            "ROYAL ENFIELD":            "royal_enfield",
            "SUZUKI":                   "suzuki",
            "Others":                   "others",
            "All":                      "*",
        },

        # 2W segment (1+5 / 5+5 grid) → canonical
        "tw_segment_make_grid": {
            "3-7 KW":       "ev_3_7kw",
            "< 3 KW":       "ev_lt3kw",
            ">3 KW":        "ev_gt3kw",
            ">7 KW":        "ev_gt7kw",
            "MC <=155":     "mc_lte155",
            "MC >155":      "mc_gt155",
            "SCOOTER":      "scooter",
            "< =350":       "re_lte350",
            "> 350":        "re_gt350",
            "All":          "*",
        },

        # 4W fuel/CC segment → canonical
        "pc_segment": {
            "Petrol<1000":      "petrol_lt1000cc",
            "Petrol>1000":      "petrol_gt1000cc",
            "Petrol 1000-1500": "petrol_1000_1500cc",
            "Petrol>1500":      "petrol_gt1500cc",
            "Diesel<1500":      "diesel_lt1500cc",
            "Diesel>1500":      "diesel_gt1500cc",
            "CNG<1000":         "cng_lt1000cc",
            "CNG>1000":         "cng_gt1000cc",
            "CNG 1000-1500":    "cng_1000_1500cc",
            "CNG >1500":        "cng_gt1500cc",
            "CNG":              "cng_all",
            "Diesel":           "diesel_all",
        },

        # 4W Comp/SAOD policy type (from compound header)
        "pc_policy_type": {
            "Non HEV Comp":     "comp",
            "Non HEV SAOD":     "saod",
            "HEV":              "hev",
        },

        # 4W Comp/SAOD fuel type (from compound header)
        "pc_fuel_type": {
            "Petrol/ EV":   "petrol_ev",
            "Diesel/ CNG":  "diesel_cng",
            "HEV":          "hev",
        },

        # CV light segment → canonical
        "cv_segment": {
            "GCV3":                         "gcv_3w",
            "GCV4 upto 1.6T":               "gcv4_lt1.6t",
            "GCV4 1.6T-2.5T":               "gcv4_1.6_2.5t",
            "GCV4 2.5 To 3.5T":             "gcv4_2.5_3.5t",
            "GCV4 3.5 To 7.5T":             "gcv4_3.5_7.5t",
            "GCV4 7.5 to 12T":              "gcv4_7.5_12t",
            "PCV3W non-diesel":             "pcv_3w_nondiesel",
            "PCV3W diesel":                 "pcv_3w_diesel",
            "E-Rickshaw":                   "e_rickshaw",
            "E-Loaders":                    "e_loader",
            "Agricultural Tractor":         "ag_tractor",
            "Misc D":                       "misc_d",
            "Backhoe loader, Forklift, Excavator, and loader": "construction_equipment",
        },

        # HCV segment → canonical
        "hcv_segment": {
            "GCV4 12 to 20T":   "gcv4_12_20t",
            "GCV4 20 to 40T":   "gcv4_20_40t",
            "GCV4 40-44T":      "gcv4_40_44t",
            "GCV4 40T+":        "gcv4_gt40t",
            "GCV4 44T+":        "gcv4_gt44t",
        },

        # School Bus operator type → canonical
        "school_bus_operator": {
            "SB - In the Name of School":  "school_name",
            "On Contract (Transporter)":   "contract_transporter",
            "On Contract (Individual)":    "contract_individual",
        },

        # Staff Bus usage type → canonical
        "staff_bus_usage": {
            "Corporate [Self-Usage]":           "corporate_self_usage",
            "Contract Carriage Transporter":    "contract_carriage_transporter",
            "Contract Carriage Individual":     "contract_carriage_individual",
        },

        # Non-Motor insurer share type → canonical
        "non_motor_insurer_share": {
            "1. Digit - 100%":      "digit_100pct",
            "2. Digit - Leader":    "digit_leader",
            "3. Digit - Follower":  "digit_follower",
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # PAYOUT BASIS
    # ─────────────────────────────────────────────────────────────────────────
    "payout_basis": {
        # 2W
        "TW_new_comp_satp_CD1":   "net_premium",      # CD1 on net/total premium
        "TW_new_comp_satp_CD2":   "net_premium",      # CD2 on net premium
        "TW_1plus5_Slab_Net5":    "net_premium",
        "TW_1plus5_MISP":         "misp_od_addon",    # MISP track — OD+Add-on basis
        "TW_5plus5_Slab_Net5":    "net_premium",
        "TW_SAOD_CD1":            "od_premium",       # SAOD CD1 on OD premium only
        "TW_SAOD_CD2":            "od_premium",       # SAOD CD2 on OD premium only

        # 4W
        "PC_TP_CD2":              "tp_premium",
        "PC_Comp_SAOD_CD2":       "od_plus_addon",    # Slab OD+AddOn with 90:10 rule
        "PC_1plus3_standard":     "od_premium",       # 19.5% on OD premium

        # CV
        "CV_light_comp":          "net_premium",
        "CV_light_tp":            "tp_premium",
        "CV_heavy_comp_CD2":      "net_premium",
        "CV_heavy_tp_CD2":        "tp_premium",
        "School_Bus_CD2":         "net_premium",
        "Staff_Bus_CD2":          "net_premium",

        # Non-Motor
        "Property_CD2":           "gross_premium",
        "Engineering_CD2":        "gross_premium",
        "WC_CD2":                 "net_premium",
        "Marine_CD2":             "net_premium",
        "Health_CD2":             "gross_premium",
    },

    # ─────────────────────────────────────────────────────────────────────────
    # DECLINED SEGMENTS
    # ─────────────────────────────────────────────────────────────────────────
    "declined_segments": [
        # 2W
        "tw_hero_motocorp_5plus5",         # HERO MOTOCORP 5+5 — All clusters = D
        "tw_mc_gt155_1plus5_selected",     # MC >155 (BAJAJ, HONDA, Royal Enfield >350) in 1+5 = MISP only
        # 4W
        "pc_tp_all_india_decline_cng_lt1000",
        "pc_tp_all_india_decline_cng_gt1000",
        "pc_tp_all_india_decline_diesel_lt1500",
        "pc_tp_all_india_decline_diesel_gt1500",
        "pc_tp_all_india_decline_petrol_lt1000",
        "pc_tp_all_india_decline_petrol_gt1000",
        # CV
        "cv_light_pcv3w_diesel_all",       # PCV3W diesel = D across all ROM clusters
        "cv_light_gcv4_12t_rom3",          # ROM3 blocks most GCV4 segments
        # HCV
        "hcv_good_vizag_12_20t_age0_1",    # Good Vizag_Vijayawada declines specific HCV age bands
        # School Bus
        "school_bus_kl_declined",
        "school_bus_tn_gt60_seater",
        "school_bus_mp",
        # Staff Bus
        "staff_bus_mp_all",
        # Non-Motor
        "retail_health_double_wallet_all_volumes",  # Retail Health = Declined across all volume slabs
        "non_motor_property_225cr_plus_non_preferred",  # No rate specified
        "non_motor_engineering_2499cr_plus",            # TBD — not a committed rate
    ],

    # ─────────────────────────────────────────────────────────────────────────
    # SPECIAL RULES
    # ─────────────────────────────────────────────────────────────────────────
    "special_rules": [
        {
            "name": "hcv_volvo_scania_block",
            "instruction": "Volvo and Scania makes are blocked (declined) for HCV across all clusters.",
            "applies_to": ["HCV"],
            "blocked_makes": ["Volvo", "Scania"],
        },
        {
            "name": "hcv_bharat_benz_mahindra_cd1_cap",
            "instruction": "For Non-Dumper/Tipper HCV: Bharat Benz and Mahindra operate at 70% CD1 ceiling.",
            "applies_to": ["HCV"],
            "makes": ["Bharat Benz", "Mahindra"],
            "cd1_ceiling_pct": 70,
        },
        {
            "name": "hcv_tata_age0_addon_variant",
            "instruction": (
                "TATA HCV Age 0: Two CD1 variants in same cell — 'With Addon: 85% / Without Addon: 60%'. "
                "Create two rules per such cell: one for each variant."
            ),
            "applies_to": ["HCV"],
            "make": "TATA",
            "age_range": [0, 0],
            "rate_variants": ["with_addon", "without_addon"],
        },
        {
            "name": "hcv_age6_plus_ncb_condition",
            "instruction": (
                "HCV Age 6+: CD1 differs by NCB/break-in status. "
                "'with NCB/break in > 90 days: 90%, without NCB: 80%'. "
                "Parse as two sub-rules with ncb_flag dimension."
            ),
            "applies_to": ["HCV"],
            "age_from": 6,
            "age_to": 99,
        },
        {
            "name": "tw_misp_track",
            "instruction": (
                "Cells showing 'MISP' (Manufacturer Incentive Scheme Price) in the CD2 column "
                "indicate OEM-MISP pricing applies. Do NOT store as a commission rate. "
                "Set commission_rate=NULL and flag rule with payout_basis='misp_od_addon'."
            ),
            "applies_to": ["2W Grid 1+5", "2W Grid 5+5"],
        },
        {
            "name": "tw_5plus5_cd2_reductions",
            "instruction": (
                "2W 5+5 grid has a 'CD2 Reductions' column with addon/CPA reduction amounts. "
                "These are not separate rules — they are conditional deductions on the CD2 rate. "
                "Capture as conditional_deductions in the grid_notes, not as separate commission_rules rows."
            ),
            "applies_to": ["2W Grid 5+5"],
            "reductions": {
                "W/O Add on":        -0.05,
                "W/O CPA":           -0.05,
                "W/o Add on and CPA": -0.10,
            },
        },
        {
            "name": "pc_comp_saod_90_10_rule",
            "instruction": (
                "4W Comp/SAOD grid applies the 90:10 rule: "
                "90% of commission is on OD premium, 10% on TP premium. "
                "The grid rate is applied to OD component. Store payout_basis='od_plus_addon'."
            ),
            "applies_to": ["New 4W Comp_SAOD"],
        },
        {
            "name": "pc_comp_1plus3_standard_rate",
            "instruction": (
                "For 1+3 policies not in the 1+1 cluster grid, apply standard rate of 19.5% on OD premium "
                "with 90:10 rule. This is a fallback rule when no cluster-specific rate exists for 1+3 products."
            ),
            "applies_to": ["PC"],
            "policy_term": "1+3",
            "fallback_rate_pct": 19.5,
        },
        {
            "name": "school_bus_short_term_reduction",
            "instruction": "Short-term School Bus policies: apply 25% reduction on the annual grid rate.",
            "applies_to": ["School Bus - Prime Brokers"],
            "short_term_reduction_pct": -25,
        },
        {
            "name": "staff_bus_short_term_reduction",
            "instruction": (
                "Short-term Staff Bus policies: apply 5% reduction on annual rate, "
                "subject to Max 45% (cap) and Min 2.5% (floor)."
            ),
            "applies_to": ["Staff Bus Grid"],
            "short_term_reduction_pct": -5,
            "short_term_max_pct": 45,
            "short_term_min_pct": 2.5,
        },
        {
            "name": "staff_bus_10_seater_and_under",
            "instruction": (
                "Staff buses with seating capacity ≤ 10: apply CD1=85%, CD2=20% in all open cohorts, "
                "regardless of the main grid rates."
            ),
            "applies_to": ["Staff Bus Grid"],
            "seating_capacity_lte": 10,
            "override_cd1_pct": 85,
            "override_cd2_pct": 20,
        },
        {
            "name": "staff_bus_new_vehicle_permit_waiver",
            "instruction": (
                "For new vehicles (Age 0), permit copy is not required. "
                "CD2 of Contract Carriage Bus goes on hold until permit copy is received."
            ),
            "applies_to": ["Staff Bus Grid"],
        },
        {
            "name": "staff_bus_electric_bus_excluded",
            "instruction": "Staff Bus grid is applicable for Non-Electric Bus only. Electric buses = Declined.",
            "applies_to": ["Staff Bus Grid"],
            "blocked_fuel_types": ["electric", "ev"],
        },
        {
            "name": "cv_short_term_rate_derivation",
            "instruction": (
                "CV Short Term Policy rates are NOT standalone rates. "
                "They are derived by applying a percentage reduction on the annual grid rates: "
                "School Bus = annual × 0.75; Staff Bus (>10) = annual × (1 − 0.05) capped at 45% / floored at 2.5%; "
                "Other CV = annual × 0.90 capped at 30% / floored at 2.5%. "
                "Do NOT create commission_rules rows for this sheet — store as a special_rule modifier only."
            ),
            "applies_to": ["CV Short term Policy grid"],
            "rate_is_modifier": True,
        },
        {
            "name": "non_motor_property_terrorism_flat",
            "instruction": "For Terrorism Premium in Property: flat 5% commission regardless of other dimensions.",
            "applies_to": ["Non-Motor Grid"],
            "sub_section": "Property Grid",
            "cover_type": "terrorism",
            "flat_rate_pct": 5.0,
        },
        {
            "name": "non_motor_property_60_40_rule",
            "instruction": (
                "60:40 brokerage split is applicable in Griha, Laghu, Sookshma products. "
                "Maximum inclusive of 60:40 is 35%."
            ),
            "applies_to": ["Non-Motor Grid"],
            "sub_section": "Property Grid",
            "products_affected": ["Griha", "Laghu", "Sookshma"],
            "max_inclusive_pct": 35,
        },
        {
            "name": "non_motor_property_1000cr_plus_individual",
            "instruction": (
                "Cases above 1000 Cr+ in Property: CD2 to be discussed individually "
                "on case-to-case basis by Steering team. Do not store a rate — flag as TBD."
            ),
            "applies_to": ["Non-Motor Grid"],
            "sub_section": "Property Grid",
            "si_above_cr": 1000,
        },
        {
            "name": "non_motor_marine_60_40_rule",
            "instruction": (
                "60:40 brokerage split is applicable for Marine Specific Policies. "
                "Maximum up to 35%."
            ),
            "applies_to": ["Non-Motor Grid"],
            "sub_section": "Marine Cargo",
            "policy_sub_type": "specific",
            "max_pct": 35,
        },
        {
            "name": "non_motor_wc_volume_bonus",
            "instruction": (
                "Workmen's Compensation: CD2 base rate + volume bonus. "
                "Monthly volume 5-10L: +1.5%; 10L+: +2.5%."
            ),
            "applies_to": ["Non-Motor Grid"],
            "sub_section": "Workmen's Compensation",
            "volume_bonus": {
                "5L_10L": 0.015,
                "10L_plus": 0.025,
            },
        },
        {
            "name": "non_motor_engineering_225cr_plus_rate_conditional",
            "instruction": (
                "Engineering proposals where DigiSafe's share > 225 Cr: "
                "final CD2 decided based on premium at conversion. "
                "Flag as rate_conditional=True and store TBD."
            ),
            "applies_to": ["Non-Motor Grid"],
            "sub_section": "Engineering Grid",
            "our_share_above_cr": 225,
        },
        {
            "name": "cv_light_cd1_band_determines_eligibility",
            "instruction": (
                "CV light sheet has From CD1 and To CD1 columns that define the broker's discount band. "
                "A CD1 range of (0, 0) means the segment is Declined. "
                "Store From CD1 and To CD1 as dimensions, not as rate values."
            ),
            "applies_to": ["CV"],
        },
    ],

    # ─────────────────────────────────────────────────────────────────────────
    # CONDITIONAL DEDUCTIONS
    # ─────────────────────────────────────────────────────────────────────────
    "conditional_deductions": [
        # None explicitly stated at a global level in this file.
        # Deductions are sheet-specific (e.g. 2W 5+5 W/O Add-on) — captured in special_rules above.
    ],

    # ─────────────────────────────────────────────────────────────────────────
    # DELTA HANDLING
    # ─────────────────────────────────────────────────────────────────────────
    "delta_handling": {
        # DigiSafe does NOT use a delta/overlay sheet in this file.
        # All sheets are self-contained. Set strategy to none.
        "strategy": "none",
    },

    # ─────────────────────────────────────────────────────────────────────────
    # EXTRACTION HINTS  (verbatim injected into LLM prompt)
    # ─────────────────────────────────────────────────────────────────────────
    "extraction_hints": r"""
DigiSafe grid is a TALL (row-per-rule) layout — one data row = one commission rule.
Do NOT transpose. Read each row directly as a rule.

FILE FORMAT: This is an ODS file (OpenDocument Spreadsheet). Use pandas with engine='odf' to open it.
openpyxl will reject it — do not attempt to use openpyxl for this insurer.

RATE COLUMNS:
- CD1 = discount/loss-ratio ceiling passed to insurer — NOT the broker commission.
- CD2 = broker commission rate (this is what goes into commission_rate in the DB).
- All rates are stored as decimals: 0.25 = 25%. Multiply by 100 before DB insert.
- Cells containing "D" (string) mean Declined — exclude these rules entirely.
- Cells containing 0.0 may be genuine zero commission or declined — check context.
  If the segment has all zeros and the cluster is named "All_India_Decline", treat as declined.
- Cells containing "MISP" mean Manufacturer Incentive Scheme Price — set commission_rate=NULL, flag payout_basis='misp_od_addon'.
- Cells containing "TBD" mean rate not committed — set commission_rate=NULL, flag as rate_conditional=True.
- Negative CD1 values in 2W SAOD are valid — they represent downward adjustments on the OD base. Do not treat as declined.

COLUMN 0: Always blank/NaN across all sheets. Skip it in every sheet.

HEADER COMPLEXITY:
- 4W Comp_SAOD has a 3-row compound header (rows 1–3): merge them to label each column as policy_type + fuel_type + ncb_flag.
- HCV sheet row 0 is a global note — do not treat it as a column header. Use row 2 as the header.
- Non-Motor Grid is a single sheet with 5 distinct sub-grids stacked vertically, separated by blank rows and section-title rows. Parse each sub-grid independently using the section anchors defined in non_motor_section_anchors.

COMPOSITE RATE STRINGS:
- Staff Bus grid rates are expressed as "CD1 95% / CD2 40%" — parse using regex r"CD1\s*([\d.]+)%\s*/\s*CD2\s*([\d.]+)%" to extract both values.
- HCV TATA Age 0 cell may contain "With Addon: 85% / Without Addon: 60%" — split into two sub-rules.
- HCV Age 6+ may contain "with NCB/break in > 90 days: 90%, without NCB: 80%" — split into two sub-rules with ncb_flag dimension.

2W CLUSTER CONCATENATION:
- Sheets '2W Grid 1+5' and '2W Grid 5+5' have a Key column (col 0) that concatenates cluster+make+segment.
- Do NOT parse the Key column — parse the separate Agency/PB Clusters, Make, and Agency/PB Seg columns instead.
- '2W SAOD' and 'New 2W Comp & SATP' have concatenated cluster+segment in col 0 too — ignore col 0, use the split columns.

NON-MOTOR EXTRACTION GUIDANCE:
- Property Grid: has two CD2 column variants (Upto 300,000 / 300,000+ for SI amount band). Create two rules per rate row.
- Engineering Grid: "TBD" rates = not committed — store commission_rate=NULL with a note.
- Retail Health: most volume slabs show 0.0 or "Declined" — exclude these. Extract only non-zero numeric CD2 values.

CV LIGHT SHEET REMARKS COLUMN:
- The 'Age' column on the far right (col 12) is a human-readable label (e.g. "Age 0", "Age 1+"). Ignore it for rule extraction — use From Age / To Age columns instead.
- The 'Remarks' column contains notes only — do not extract as a dimension.

SCHOOL BUS TERMS & CONDITIONS (rows 31–34):
- Rows after the data grid contain Important Terms & Conditions — do NOT extract as commission rules. Extract as grid_notes with note_category=SPECIAL_RULE.

STAFF BUS DOCUMENTS REQUIRED (cols 7–8):
- Columns labelled "Documents required - Corporate Self-Usage" and "Documents required - Contract Carriage" contain text notes, not rates. Extract as grid_notes.

CV SHORT TERM POLICY GRID:
- This sheet contains MODIFIERS (percentage reductions on annual rates), not standalone rates. Do NOT create commission_rules rows. Capture as special_rule modifiers only.

DIMENSION KEY VALUES MUST USE CANONICAL NAMES from dimension_aliases in this profile. Do not invent new keys.
""",

    # ─────────────────────────────────────────────────────────────────────────
    # INGESTION PIPELINE HINTS
    # ─────────────────────────────────────────────────────────────────────────
    "ingestion_hints": {
        "file_type": "ods",
        "open_with": "pandas.read_excel(engine='odf')",
        "parse_strategy": "tall_row_per_rule",
        "skip_col_index": 0,
        "multi_section_sheet": "Non-Motor Grid",
        "modifier_only_sheets": ["CV Short term Policy grid"],
        "note_only_rows_by_sheet": {
            "School Bus - Prime Brokers": "rows_31_onwards",
            "Staff Bus Grid":             "rows_17_onwards",
            "HCV":                        "row_0",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# QUICK SANITY CHECK
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    # Verify the profile is serialisable (required for JSONB storage)
    serial = json.dumps(DIGISAFE_SCHEMA_PROFILE, indent=2, default=str)
    print(f"Profile serialises cleanly. Length: {len(serial):,} chars")

    print("\nActive sheets:")
    for sheet, key in DIGISAFE_SCHEMA_PROFILE["file_structure"]["active_sheets"].items():
        product = DIGISAFE_SCHEMA_PROFILE["product_line_map"].get(sheet, "?")
        print(f"  {sheet:<35} → role={key:<25} product_line={product}")

    print("\nProduct lines:", DIGISAFE_SCHEMA_PROFILE["product_lines"])
    print("Special rules:", [r["name"] for r in DIGISAFE_SCHEMA_PROFILE["special_rules"]])
    print("Declined segments:", len(DIGISAFE_SCHEMA_PROFILE["declined_segments"]))
