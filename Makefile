# Simple local entrypoints

.PHONY: test check docker-up docker-down test-e2e test-all clean

test:
	uv run pytest -q -m unit

check:
	uv run ruff check .
	uv run pyright inventory/ tests/

docker-up:
	@if [ ! -f docker/api_key.txt ] || [ -d docker/api_key.txt ]; then \
		rm -rf docker/api_key.txt; \
		touch docker/api_key.txt; \
	fi
	cd docker && docker compose up -d

docker-down:
	cd docker && docker compose down -v
	rm -rf docker/api_key.txt
	touch docker/api_key.txt

test-e2e:
	$(MAKE) docker-up
	./docker/wait-for-snipeit.sh
	uv run pytest tests/e2e -q -m integration

test-all:
	$(MAKE) test
	$(MAKE) test-e2e
	$(MAKE) check

clean:
	rm -rf .pytest_cache .ruff_cache
	$(MAKE) docker-down
