.PHONY: install lint test backtest mc-sweep report clean

install:
	pip install -e ".[dev]"

lint:
	ruff check src/ tests/ scripts/

test:
	pytest

test-slow:
	RUN_SLOW=1 pytest

backtest:
	python -m nqbreakout.cli.backtest --config configs/strategy_default.yaml

mc-sweep:
	python -m nqbreakout.cli.mc_sweep --accounts-config configs/apex_accounts.yaml --sims 2000

report:
	python scripts/build_report.py

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .ipynb_checkpoints -exec rm -rf {} +
