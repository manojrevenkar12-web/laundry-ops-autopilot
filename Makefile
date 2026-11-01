.PHONY: install test eval eval-noisy demo serve docker
install: ; pip install -e ".[dev]"
test: ; pytest -q
eval: ; python -m laundryops.cli eval --n 500 --seed 42 --gate
eval-noisy: ; python -m laundryops.cli eval --n 500 --seed 42 --noise 0.5
demo: ; python -m laundryops.cli demo
serve: ; python -m laundryops.cli serve --reload
docker: ; docker compose up --build
