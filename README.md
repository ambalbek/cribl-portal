# ELK-ARO Onboarding Portal

A lightweight, client-facing Flask app where teams submit application onboarding requests. Each submission is stored as a document in a dedicated Elasticsearch index for tracking and review.

Clients cannot deploy anything — they only file a request. The platform team handles the actual onboarding via the internal [Cribl Pusher](../cribl-flask) tool, which automatically marks requests as **done** when it finishes.

---

## What It Does

Clients fill in:
- **App ID** — application identifier
- **App Name** — single word, underscores only (e.g. `payments_service`)
- **Region** — `azn` (Azure North) or `azs` (Azure South)
- **Entitlement Groups** — one or more ELK role-mapping groups, typed as tags

On submit, the request is written to the `cribl-onboarding-requests` Elasticsearch index with status `pending` and a unique Request ID (`REQ-YYYYMMDD-XXXXXXXX`) returned to the client.

When the platform team completes onboarding via cribl-flask, the status is automatically updated to `done` via the admin API.

---

## Request Lifecycle

```
Client submits form
        │
        ▼
  status: pending  ──────────────────────────────────────────────────────────┐
        │                                                                    │
        ▼                                                                    │
Platform team opens cribl-flask, enters REQ-YYYYMMDD-XXXXXXXX               │
        │                                                                    │
        ▼                                                                    │
Routes + Destinations + ELK Roles + Role Mappings pushed                    │
        │                                                                    │
        ▼                                                                    │
cribl-flask calls POST /admin/update-status  ───────────────────────────────┘
        │
        ▼
  status: done
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Create your config file

```bash
cp config.example.json config.json
```

Edit `config.json`:

```json
{
  "admin_secret": "a-strong-random-secret",
  "datastream": {
    "elk_url":  "https://your-elk:9200",
    "token":    "<api-key-encoded-value>",
    "username": "",
    "password": "",
    "index":    "cribl-onboarding-requests",
    "skip_ssl": false,
    "timeout":  30
  }
}
```

Use `token` (recommended) **or** `username`/`password`. Token takes priority if both are set.

### 3. Run

```bash
python app.py
```

Opens on `http://localhost:9229`.

---

## Docker

### Recommended — Docker Compose

```bash
cp config.example.json config.json
# fill in config.json

docker compose up -d
```

### Manual

```bash
docker build -t cribl-portal .

docker run -d --name cribl-portal \
  -p 9229:9229 \
  -v $(pwd)/config.json:/app/config.json:ro \
  cribl-portal
```

### Useful commands

```bash
# Logs
docker logs -f cribl-portal

# Health
docker inspect --format='{{.State.Health.Status}}' cribl-portal

# Stop and remove
docker stop cribl-portal && docker rm cribl-portal

# Rebuild after code changes
docker compose up -d --build
```

> `config.json` is **never baked into the image** — it is always mounted at runtime as a read-only volume.

---

## Elasticsearch — Index Setup

### Step 1 — Apply the index template

Run once in **Kibana → Management → Dev Tools** (or via curl) **before** the first document is indexed.

Using the provided `elk-index-template.json`:

```bash
curl -X PUT "https://your-elk:9200/_index_template/cribl-onboarding-requests" \
  -H "Content-Type: application/json" \
  -H "Authorization: ApiKey <token>" \
  -d @elk-index-template.json
```

Or paste directly into Kibana Dev Tools:

```json
PUT _index_template/cribl-onboarding-requests
{
  "index_patterns": ["cribl-onboarding-requests*"],
  "priority": 100,
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 1
    },
    "mappings": {
      "dynamic": "strict",
      "properties": {
        "@timestamp":         { "type": "date" },
        "request_id":         { "type": "keyword" },
        "apmid":              { "type": "keyword" },
        "appname":            { "type": "keyword" },
        "region":             { "type": "keyword" },
        "entitlement_groups": { "type": "keyword" },
        "status":             { "type": "keyword" }
      }
    }
  }
}
```

All fields are `keyword` — exact match, no `.keyword` suffix needed in queries. `dynamic: strict` rejects any unknown fields at index time.

> **Important:** Apply this before the index is created. If the index already exists, you will need to reindex to pick up the new mappings.

### Step 2 — Create the writer role

```json
PUT _security/role/cribl_portal_writer
{
  "cluster": ["monitor"],
  "indices": [
    {
      "names": ["cribl-onboarding-requests*"],
      "privileges": ["create_doc", "create_index", "auto_configure", "write"]
    }
  ]
}
```

### Step 3 — Create the API key

```json
POST _security/api_key
{
  "name": "cribl-portal-ingest",
  "role_descriptors": {
    "cribl_portal_writer": {
      "cluster": ["monitor"],
      "indices": [
        {
          "names": ["cribl-onboarding-requests*"],
          "privileges": ["create_doc", "create_index", "auto_configure", "write"]
        }
      ]
    }
  }
}
```

Copy the `encoded` value from the response and set it as `datastream.token` in `config.json`.

### Privilege breakdown

| Privilege | Why |
|---|---|
| `create_doc` | Index new documents (`POST /_doc`) |
| `create_index` | Create the index on first write |
| `auto_configure` | Apply matching index templates automatically |
| `write` | Required for `_update_by_query` (status updates from cribl-flask) |
| `monitor` (cluster) | Required for basic health checks |

---

## Admin API

The admin endpoint is used by **cribl-flask** to automatically update request status after onboarding completes. It is not exposed to clients.

### Update request status

```
POST /admin/update-status
Header: X-Admin-Secret: <admin_secret from config.json>
```

```json
{
  "request_id": "REQ-20260320-ABCD1234",
  "status": "done"
}
```

Valid statuses: `pending`, `done`, `rejected`

The endpoint performs an Elasticsearch `_update_by_query` with a `term` filter on `request_id` (keyword field — direct inverted index lookup, no scan):

```json
{
  "query":  { "term": { "request_id": "REQ-20260320-ABCD1234" } },
  "script": { "source": "ctx._source.status = 'done'", "lang": "painless" }
}
```

---

## Index document shape

```json
{
  "@timestamp":         "2026-03-20T10:00:00+00:00",
  "request_id":         "REQ-20260320-A1B2C3D4",
  "apmid":              "app00001234",
  "appname":            "payments_service",
  "region":             "azn",
  "entitlement_groups": ["group-a", "group-b"],
  "status":             "pending"
}
```

---

## Serving via Apache httpd (bastion)

Docker runs on the **remote host**. Apache runs on the **bastion** and reverse-proxies to it.

```
Browser → https://bastion/portal/
          Apache ProxyPass → http://REMOTE_HOST_IP:9229/
          Docker container → Flask :9229
```

Add the contents of `httpd-add-to-existing.conf` inside your existing `<VirtualHost>` block:

```bash
sudo vi /etc/httpd/conf.d/your-existing.conf
sudo httpd -t && sudo systemctl reload httpd
```

Replace `REMOTE_HOST_IP` with the IP of the host running Docker.

| URL | What |
|---|---|
| `https://bastion/portal/` | Client onboarding request form |
| `https://bastion/portal/admin/update-status` | Admin status update endpoint |
| `https://bastion/health` | Health check |

### Required Apache modules

```bash
httpd -M | grep -E 'proxy|headers'
# proxy_module, proxy_http_module, headers_module must be listed
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FILE` | *(none)* | Path to log file — appended across runs |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` `/portal` `/portal/` | Client onboarding form |
| `POST` | `/portal/api/submit` `/api/submit` | Submit a new onboarding request |
| `GET` | `/portal/admin/update-status` | Admin update form |
| `POST` | `/portal/admin/update-status` `/admin/update-status` | Update request status (requires `X-Admin-Secret`) |
| `GET` | `/health` | Liveness check — returns `ok` |
| `GET` | `/health/es` | Elasticsearch connectivity check |
