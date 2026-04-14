# Flood Prediction Engine — Dangkor District, Phnom Penh

Autonomous 72-hour flood prediction engine for Dangkor District (ខណ្ឌដង្កោ), Phnom Penh, Cambodia. Runs every 6 hours on GitHub Actions, produces per-grid-cell flood probability and risk levels, and publishes a static dashboard via GitHub Pages.

**Live Dashboard:** [chhounk.github.io/flood-predict-dangkor](https://chhounk.github.io/flood-predict-dangkor/)

> **Disclaimer:** This is a model-based forecast, NOT an official flood warning. See [LIMITATIONS.md](LIMITATIONS.md) before interpreting results.

## How It Works

The engine divides Dangkor District into a 250m grid (~2,500 cells) and runs a physics-based flood prediction chain every 6 hours:

1. **Meteorological Driver** — Fetches 72-hour rainfall forecasts from three global weather models (ECMWF, GFS, ICON) via Open-Meteo. Model disagreement quantifies forecast uncertainty.

2. **Hydrological Response** — Converts rainfall to runoff using the SCS Curve Number method (based on land cover and soil type), routes water downhill using DEM-derived flow paths, and flags cells where accumulated water exceeds drainage capacity. A 30-member Monte Carlo ensemble captures parameter uncertainty.

3. **External Signals** — Cross-checks forecasts against satellite-observed rainfall (NASA GPM IMERG). Future: GloFAS river flood forecasts.

4. **Fusion & Classification** — Combines physics probability with regional signals and classifies each cell into 4 risk levels (Safe / Low / Moderate / High) across five time windows (6h / 12h / 24h / 48h / 72h).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full data flow diagram.

## Data Sources

| Source | What | License | URL |
|--------|------|---------|-----|
| Open-Meteo | 72h hourly rainfall forecasts (multi-model) | CC-BY 4.0 | https://open-meteo.com/ |
| Open-Meteo Archive | 7-day historical rainfall | CC-BY 4.0 | https://open-meteo.com/ |
| NASA GPM IMERG | Satellite-observed rainfall (Late Run) | Open | https://gpm.nasa.gov/data/imerg |
| Copernicus DEM GLO-30 | 30m digital elevation model | Free & Open | https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model |
| ESA WorldCover 2021 | 10m land cover classification | CC-BY 4.0 | https://esa-worldcover.org/ |
| GADM | Administrative boundaries (level 2) | Free for non-commercial | https://gadm.org/ |
| GloFAS | River flood forecasts (stubbed in v1) | Copernicus License | https://global-flood.emergency.copernicus.eu/ |

## Quick Start

```bash
# Clone and install
git clone https://github.com/chhounk/flood-predict-dangkor.git
cd flood-predict-dangkor
make setup

# Build the grid (one-time)
make grid

# Process static terrain data (one-time)
make static

# Run the prediction pipeline
make predict

# Run tests
make test
```

## Reading the Dashboard

- **Map:** Each colored cell shows the flood risk level for that area.
- **Time windows:** Use the 6h / 12h / 24h / 48h / 72h buttons to see peak risk within each forecast window.
- **Click a cell** to see grid ID, commune name, probability, and peak risk time.
- **Color coding:** Gray = Safe, Yellow = Low, Orange = Moderate, Red = High.
- **Model confidence** shows how well satellite observations match recent forecasts.

## Engine Version

Current: `engine-v0.1`

## License

Code: MIT. Data sources retain their original licenses (see table above).
