FROM apache/airflow:2.10.5-python3.12

# Airflow 2.10 owns SQLAlchemy 1.4. The catalog domain uses SQLAlchemy 2.x.
# Keep both dependency graphs intact and run catalog tasks with ExternalPythonOperator.
USER root
RUN apt-get update \
    && apt-get install --yes --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/* \
    && python -m virtualenv /opt/catalog-venv \
    && /opt/catalog-venv/bin/pip install --no-cache-dir \
        "dill==0.3.9" \
        "psycopg[binary]>=3.2,<4" \
        "SQLAlchemy>=2.0,<3"
USER airflow
