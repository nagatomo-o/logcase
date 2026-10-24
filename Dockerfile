FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

# Corporate TLS inspection: pip cannot verify PyPI's certificate inside the container.
ENV PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org pypi.python.org"
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV ACCESS_LOG_BIND=0.0.0.0

RUN pip install --no-cache-dir --root-user-action=ignore "flask>=3.0" "duckdb>=1.1"

EXPOSE 8765

CMD ["python", "-m", "logcase.app"]
