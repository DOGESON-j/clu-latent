# CLU Latent HTTP API v0

CLU Latent's HTTP layer exposes the existing V1 ingest and read-only package APIs without changing the `.clulatent` format.

## Run locally

Install the API extra:

```bash
python -m pip install -e ".[api]"
export CLULATENT_API_KEY="replace-with-a-long-random-secret"
uvicorn clu_latent.http_api:app --host 0.0.0.0 --port 8000
```

OpenAPI docs are available at `/docs` and the machine-readable schema at `/openapi.json`.

## Docker

```bash
docker build -t clu-latent-api .
docker run --rm -p 8000:8000 \
  -e CLULATENT_API_KEY="replace-with-a-long-random-secret" \
  -v clu-latent-data:/data/clulatent-api \
  clu-latent-api
```

The service is deliberately provider-neutral. Any platform or deployment agent that can run this Dockerfile and mount persistent storage can host it.

## Authentication

Every endpoint except `GET /v1/health` requires:

```text
Authorization: Bearer YOUR_API_KEY
```

Set the key with `CLULATENT_API_KEY`. Anonymous access is disabled unless `CLULATENT_ALLOW_ANONYMOUS=1` is explicitly set.

## Create a job

```bash
curl -X POST http://localhost:8000/v1/jobs \
  -H "Authorization: Bearer $CLULATENT_API_KEY" \
  -F "file=@input.mp4"
```

Response:

```json
{"id":"job_...","status":"queued","profile":"clulatent.profile.v1"}
```

Poll the job:

```bash
curl http://localhost:8000/v1/jobs/job_... \
  -H "Authorization: Bearer $CLULATENT_API_KEY"
```

## Read evidence

Package summary:

```bash
curl http://localhost:8000/v1/jobs/job_.../summary \
  -H "Authorization: Bearer $CLULATENT_API_KEY"
```

Bounded time window around 17 seconds:

```bash
curl "http://localhost:8000/v1/jobs/job_.../window?at_ms=17000&radius_ms=2000" \
  -H "Authorization: Bearer $CLULATENT_API_KEY"
```

Download the deterministic portable package:

```bash
curl -L http://localhost:8000/v1/jobs/job_.../artifact \
  -H "Authorization: Bearer $CLULATENT_API_KEY" \
  -o result.clulatent
```

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `CLULATENT_API_KEY` | unset | Bearer token required for protected endpoints |
| `CLULATENT_ALLOW_ANONYMOUS` | false | Explicitly disable auth for development/testing |
| `CLULATENT_DATA_DIR` | `/tmp/clulatent-api` | Job, upload, package, and artifact storage |
| `CLULATENT_MAX_UPLOAD_BYTES` | `104857600` | HTTP upload limit (100 MiB) |
| `CLULATENT_MAX_WORKERS` | `2` | In-process ingest workers |
| `CLULATENT_MAX_WINDOW_EVENTS` | `500` | Maximum events returned from one time-window request |
| `CLULATENT_MAX_WINDOW_RADIUS_MS` | `60000` | Largest accepted evidence-window radius |

## Deployment boundary

This v0 worker is intentionally simple and deployable: one service process plus persistent storage. It is suitable for an initial public/private beta and low concurrency.

For multi-instance production scale, replace the in-process executor and local job metadata with a durable queue/database/object store. The HTTP contract can remain the same.

CLU Latent V1 provides bounded evidence. This API does not add object recognition, identity recognition, scene interpretation, or semantic-certification claims.
