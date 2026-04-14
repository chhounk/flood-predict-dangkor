# Limitations

Read this document before trusting any output from the flood prediction engine.

## Critical Limitations

### No Levee or Pumping Station Data
The model has no information about flood control infrastructure (levees, dikes, pumping stations, drainage canals). Dangkor has evolving urban drainage infrastructure that significantly affects real-world flood behavior. The model cannot account for engineered flood protection or its failure.

### Urban Drainage is Approximated
Drainage capacity per cell is a single configurable value (default 500 m3 per timestep), not derived from actual drainage network data. Real urban drainage varies enormously by block. Pipe capacity, clogging, and pump station outages are not modeled.

### 250m Grid Hides Sub-Block Detail
A 250m cell covers ~6 hectares. Flooding can vary dramatically within a single cell — one side of a street may flood while the other doesn't. The model produces cell-averaged probabilities, not point predictions.

### Monte Carlo Perturbation Bounds Are Assumptions
- Rainfall perturbation: ±10% lognormal — not validated against actual forecast error distributions for this region.
- Curve Number perturbation: ±10% — not validated against field measurements in Dangkor.
- Drainage capacity perturbation: ±20% — engineering estimate, not measured.

These bounds significantly affect the probability distribution. Different bounds would produce different probabilities.

### Probabilities Are Model Probabilities, Not Empirically Calibrated
The probability that a cell is "flooded" means "X out of 30 Monte Carlo scenarios flagged this cell." This is NOT the same as "X% of the time this cell actually floods when conditions like this occur."

Until run history is compared against observed flooding (via Sentinel-1 SAR imagery or ground truth), treat Level 4 as "the model says very likely" — not as a calibrated 85%+ probability.

### Flat Terrain Challenges
Dangkor sits on a flat clay floodplain near the Mekong/Bassac confluence. On flat terrain:
- DEM-derived flow routing is unreliable (elevation differences approach DEM noise floor).
- HAND values may be ambiguous.
- The bathtub fallback model (local ponding) compensates partially but is simplistic.

### Limited Weather Model Coverage
Only three NWP models are used (ECMWF IFS, GFS, ICON). Tropical convective rainfall is notoriously hard to predict — model spread underestimates true forecast uncertainty, especially for localized thunderstorms.

### Regional Signal Plugins Are Incomplete
- **GPM IMERG**: Implemented but only as a confidence indicator, not a probability corrector.
- **GloFAS**: Stubbed only — river flood conditions from upstream Mekong/Bassac are not factored into predictions. This is a significant gap for Dangkor, which is at the confluence.

### Validation Framework Is Scaffolded, Not Populated
The engine includes a validation framework structure but no actual validation data. Sentinel-1 SAR flood extent comparison is a documented TODO. No hindcast verification has been performed.

### No Tidal or River Stage Input
Dangkor is influenced by Tonle Sap / Mekong / Bassac water levels and tidal effects. The model does not incorporate river gauge data or tidal predictions. During the wet season, elevated river levels significantly reduce drainage capacity — this effect is not modeled.

### Soil Moisture Is Approximate
Antecedent moisture condition (AMC) is derived from the last 5 days of modeled rainfall, not from satellite soil moisture products (e.g., SMAP, SMOS) or ground measurements. The SCS AMC adjustment is coarse (three classes: dry/normal/wet).

### Static Land Cover
ESA WorldCover 2021 is used as-is. Land cover in Dangkor's peri-urban fringe changes rapidly due to development. Recently built-up areas may still be classified as cropland.

## Summary

This engine is a research-grade prototype. It provides useful directional signals about flood risk, but its outputs must not be used as the sole basis for emergency decisions. Always cross-reference with official warnings from Cambodia's Ministry of Water Resources and Meteorology and the Mekong River Commission.
