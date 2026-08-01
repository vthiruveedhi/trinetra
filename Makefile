# Cross-platform helpers (Mac/Linux; Windows use scripts\*.ps1)
.PHONY: setup test demo stop smoke

setup:
	bash scripts/mac/setup_mac.sh

test:
	.venv/bin/python -m pytest -q

demo:
	bash scripts/mac/start_demo.sh

stop:
	bash scripts/mac/stop_demo.sh

smoke:
	.venv/bin/python -m rasaops_edge.scripts.smoke_capture --frames 2
	.venv/bin/python -m rasaops_edge.scripts.run_pipeline_smoke --frames 12
