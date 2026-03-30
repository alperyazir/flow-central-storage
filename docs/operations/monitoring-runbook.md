# Monitoring Runbook

## Overview

Prometheus and Grafana provide runtime visibility into Flow Central Storage. Prometheus scrapes metrics exposed by the FastAPI service at `/metrics`; Grafana visualises the data via the "Flow Central API Overview" dashboard.

## Components

- **Prometheus** – collects metrics from `api:8000/metrics`.
- **Grafana** – displays dashboards sourced from Prometheus.
- **FastAPI metrics** – the backend serves Prometheus exposition format using the `prometheus_client` registry.

## Deployment Steps

1. Ensure the API service has been redeployed with the new metrics middleware.
2. Update `infrastructure/docker-compose.yml` in production to include the `prometheus` and `grafana` services (enable the `monitoring` profile if using profiles).
3. Copy the following assets to the server:
   - `infrastructure/monitoring/prometheus.yml` → `/opt/dream-central/monitoring/prometheus.yml`
   - `infrastructure/monitoring/dashboards/dream-central-api.json` → `/opt/dream-central/monitoring/dashboards/`
4. Start the monitoring stack:

```bash
cd /opt/dream-central/infrastructure
docker compose --profile monitoring up -d prometheus grafana
```

## Configuration

- **Prometheus**: listens on port `9091` (maps to container `9090`). Scrape interval is 15s by default.
- **Grafana**: accessible on port `3000` with default credentials `admin/admin`. Change the password immediately after first login.
- **Dashboard import**: in Grafana, navigate to *Dashboards → Import*, select the JSON file located at `/var/lib/grafana/dashboards/dream-central-api.json`, and set the Prometheus data source.

## Validation

1. Open `http://<host>:9091/targets` – ensure the `dream-central-api` scrape target is UP.
2. Visit `http://<host>:3000` – log in and open the "Flow Central API Overview" dashboard.
3. Confirm panels show data for request rate, error rate, and latency.
4. Trigger sample requests (e.g., `curl http://<host>:8000/health`) and verify metrics update within ~15 seconds.

## Troubleshooting

- **Prometheus target DOWN**: check API container connectivity and ensure `/metrics` is reachable without auth. Inspect Prometheus logs via `docker logs prometheus`.
- **Grafana cannot connect to Prometheus**: configure the Prometheus data source URL (`http://prometheus:9090`).
- **Metrics missing**: confirm FastAPI app logs indicate metrics middleware initialisation; ensure the `/metrics` endpoint returns Prometheus formatted text.

## Maintenance

- Back up Grafana persistent storage (`grafana_data` volume) before upgrading.
- Review dashboards quarterly to incorporate new metrics or services.
- Consider creating alert rules as follow-up work once Prometheus is stable.
