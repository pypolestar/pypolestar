SOURCE=		pypolestar

all:

lint:
	uv run ruff check $(SOURCE)

reformat:
	uv run ruff check --select I --fix $(SOURCE) tests
	uv run ruff format $(SOURCE) tests

test:
	uv run pytest --ruff --ruff-format
