SOURCE=		pypolestar

all:

lint:
	ruff check $(SOURCE)

reformat:
	ruff check --select I --fix $(SOURCE) tests
	ruff format $(SOURCE) tests

test:
	poetry run pytest --ruff --ruff-format
