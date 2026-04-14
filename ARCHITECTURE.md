# Architecture

## 4-Layer Engine Design

```
┌─────────────────────────────────────────────────────────┐
│                    LAYER 1: METEOROLOGICAL               │
│                                                          │
│  Open-Meteo API ──► 3 NWP Models (ECMWF, GFS, ICON)    │
│       │                    │                             │
│       ▼                    ▼                             │
│  9 Sample Points ──► IDW Interpolation ──► 250m Grid    │
│       │                                                  │
│  Archive API ──► 7-day history ──► Antecedent Moisture  │
│                                                          │
│  Output: per-cell rainfall forecast + model spread       │
│          per-cell antecedent moisture condition           │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                 LAYER 2: HYDROLOGICAL RESPONSE           │
│                                                          │
│  ┌─── Static (computed once) ──────────────────────┐    │
│  │  DEM ──► Fill ──► D8 Flow Dir ──► Flow Accum   │    │
│  │  DEM ──► HAND (Height Above Nearest Drainage)   │    │
│  │  WorldCover ──► CN Map (via cn_lookup.yaml)     │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─── Monte Carlo (30 scenarios) ──────────────────┐    │
│  │  For each scenario:                              │    │
│  │    1. Sample one NWP model + perturb rainfall    │    │
│  │    2. SCS Curve Number ──► Runoff per cell       │    │
│  │    3. Flow accumulation ──► Upstream runoff      │    │
│  │    4. Inundation flagging:                       │    │
│  │       - HAND < 2m AND accum > drainage cap       │    │
│  │       - OR local runoff > local storage          │    │
│  └─────────────────────────────────────────────────┘    │
│                                                          │
│  Output: physics_P = (# flooded scenarios) / 30          │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              LAYER 3: EXTERNAL SIGNALS                   │
│                                                          │
│  RegionalSignal plugin interface (base.py)               │
│       │                                                  │
│       ├── GPM IMERG ──► forecast-vs-observation          │
│       │                  agreement factor (0-1)          │
│       │                                                  │
│       └── GloFAS (stub) ──► returns None in v1           │
│                                                          │
│  Output: regional_amplifier + confidence indicators      │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              LAYER 4: FUSION + CLASSIFICATION            │
│                                                          │
│  final_P = clip(physics_P × regional_amplifier, 0, 1)   │
│                                                          │
│  Classification (thresholds.yaml):                       │
│    L1 Safe:     P < 0.10                                 │
│    L2 Low:      0.10 ≤ P < 0.40                          │
│    L3 Moderate: 0.40 ≤ P < 0.85                          │
│    L4 High:     P ≥ 0.85                                 │
│                                                          │
│  Window peaks: 6h / 12h / 24h / 48h / 72h               │
│                                                          │
│  Output: latest.json + latest.geojson                    │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                      OUTPUTS                             │
│                                                          │
│  data/outputs/latest.json ──► Future Telegram bot        │
│  data/outputs/latest.geojson ──► Dashboard map layer     │
│  data/outputs/history/<timestamp>.json ──► ML training   │
│  docs/data/ ──► GitHub Pages static dashboard            │
└─────────────────────────────────────────────────────────┘
```

## Data Flow

```
Open-Meteo ──┐
             ├──► Layer 1 ──► Gridded rainfall + AMC
GPM IMERG ───┤
             │
DEM ─────────┤
WorldCover ──┼──► Layer 2 ──► physics_P per cell per timestep
Grid ────────┤
             │
GPM signal ──┼──► Layer 3 ──► regional_amplifier + confidence
GloFAS stub ─┘
                    │
                    ▼
              Layer 4 ──► final_P ──► risk levels ──► JSON/GeoJSON
                                                          │
                                                          ▼
                                                    GitHub Pages
                                                     Dashboard
```

## Extension Points

### Adding a New District
1. Create a new `config/district.yaml` with the district's boundary, bbox, sample points, and communes.
2. Download the admin boundary to `data/static/`.
3. Run `make grid` and `make static` to generate the grid and terrain data.
4. The pipeline reads all configuration from YAML — no code changes needed.

### Adding a New Regional Signal Plugin
1. Create a new class in `src/signals/` that inherits from `RegionalSignal` (defined in `src/signals/base.py`).
2. Implement `fetch()` → returns a scalar 0–1 risk per timestep, or `None` if unavailable.
3. Register the plugin in `src/fusion/combine.py`.
4. The fusion layer automatically incorporates new signals via the `regional_amplifier`.

### Adding an ML Correction Layer (Future)
The architecture separates physics-based probability (`physics_P`) from the final output. An ML model can be inserted between Layer 2 and Layer 4:

```
physics_P ──► ML correction ──► corrected_P ──► Layer 4
```

The ML model would be trained on `data/outputs/history/` (predictions) vs `data/validation/` (observations). The `physics_P` remains available as a feature, and the ML layer adjusts it based on learned biases.

### Adding Telegram Bot (Future)
The bot consumes `data/outputs/latest.json`, which contains:
- Per-cell risk levels and probabilities
- Window peaks for each time horizon
- Grid cell centroids for point-interpolation (`risk_at_point()`)
- Summary statistics for district-wide alerts

The JSON schema is stable and versioned (`engine_version`).

## Runtime Profile

| Stage | Estimated Duration |
|-------|-------------------|
| Data fetch (Open-Meteo + GPM) | ~25s |
| Load static data | ~5s |
| IDW interpolation | ~2s |
| Monte Carlo (30 scenarios) | ~15s |
| Fusion + classification | ~2s |
| Write outputs | ~3s |
| Git commit + push | ~10s |
| **Total** | **~60s** |

Target: under 10 minutes on GitHub Actions free tier.
