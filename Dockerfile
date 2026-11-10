FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
    ECCLES_MODELS=/app/models ECCLES_RUNTIME=/app/runtime
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
RUN useradd --uid 10001 --create-home eccles
USER 10001:10001
EXPOSE 18771
CMD ["uvicorn", "eccles_service.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "18771", "--workers", "1", "--no-access-log"]
