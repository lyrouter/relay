# Convenience targets. Everything here also runs in CI (.github/workflows/ci.yml);
# nothing in CI is missing from here, so a green `make gates` means a green build.

.PHONY: install db-bootstrap migrate serve test gates registry clean

install:
	uv venv --python 3.12
	uv pip install -e ".[dev]"

db-bootstrap:
	sudo -u postgres psql -p 5433 -v ON_ERROR_STOP=1 -f scripts/bootstrap_db.sql
	- sudo -u postgres createdb -p 5433 -O relay_owner relay_dev
	- sudo -u postgres createdb -p 5433 -O relay_owner relay_test
	for db in relay_dev relay_test; do \
	  sudo -u postgres psql -p 5433 -d $$db -c "GRANT USAGE ON SCHEMA public TO relay_app, relay_system;"; \
	  sudo -u postgres psql -p 5433 -d $$db -c "GRANT CREATE ON SCHEMA public TO relay_owner;"; \
	  sudo -u postgres psql -p 5433 -d $$db -f scripts/bootstrap_extensions.sql; \
	done

migrate:
	uv run alembic upgrade head

# The HTTP layer (WEB). A factory rather than a module-level app so settings are
# read per instance; --reload is for development only.
serve:
	uv run uvicorn relay.api.app:create_app --factory --reload --port 8000

registry:
	uv run python scripts/gen_entity_registry.py

test:
	uv run pytest -q

# The three blocking gates, in the order CI runs them.
gates:
	uv run ruff check .
	uv run lint-imports
	uv run python scripts/gen_entity_registry.py --check
	uv run pytest -q
