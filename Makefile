.PHONY: help build up down restart logs sh test lint package-offline

help:
	@echo "Available targets:"
	@echo "  build            Build the Docker image"
	@echo "  up               Start the stack (detached)"
	@echo "  down             Stop and remove containers"
	@echo "  restart          Restart the app container (picks up code-mount changes)"
	@echo "  logs             Tail application logs"
	@echo "  sh               Open a shell inside the running container"
	@echo "  test             Run the test suite inside the container"
	@echo "  lint             Run ruff/mypy"
	@echo "  package-offline  Build an offline deployment bundle"

build:
	docker compose build

up:
	bash scripts/start.sh

down:
	bash scripts/stop.sh

restart:
	bash scripts/restart.sh

logs:
	docker compose logs -f app

sh:
	docker compose exec app /bin/bash

test:
	docker compose exec app pytest

lint:
	docker compose exec app ruff check .
	docker compose exec app mypy app

package-offline:
	bash scripts/package_offline.sh
