FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY laundryops ./laundryops
RUN pip install --no-cache-dir -e .
ENV LOPS_DB_PATH=/data/laundryops.sqlite
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "laundryops.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
