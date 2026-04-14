.PHONY: setup predict test dash clean grid static

# Install dependencies
setup:
	pip install -r requirements.txt

# Run the full prediction pipeline
predict:
	python -m src.pipeline

# Run tests
test:
	python -m pytest tests/ -v

# Build the grid (one-time setup)
grid:
	python -m src.grid.build_grid

# Process static terrain data (one-time setup)
static:
	python -m src.data.load_static

# Copy outputs to dashboard
dash:
	cp data/outputs/latest.json docs/data/latest.json
	cp data/outputs/latest.geojson docs/data/latest.geojson
	@[ -f data/validation/latest.json ] && cp data/validation/latest.json docs/data/validation_latest.json || true
	@echo "Dashboard data updated in docs/data/"

# Clean generated outputs (keeps static data)
clean:
	rm -f data/outputs/latest.json data/outputs/latest.geojson
	rm -rf data/outputs/history/*
