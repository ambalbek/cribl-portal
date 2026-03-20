# Cribl Portal — Application Onboarding Request Portal

A lightweight, client-facing Flask app where teams submit application onboarding requests. Each submission is stored as a document in a dedicated Elasticsearch datastream for tracking and review.

Clients cannot deploy anything — they only file a request. The platform team handles the actual onboarding via the internal [Cribl Pusher](../cribl-flask) tool.

---

## What It Does

Clients fill in:
- **App ID** — application identifier
- **App Name** — single word, underscores only (e.g. `payments_service`)
- **Region** — `azn` (Azure North) or `azs` (Azure South)
- **Entitlement Groups** — one or more ELK role-mapping groups, typed as tags

On submit, the request is written to the `cribl-onboarding-requests` Elasticsearch datastream with status `pending` and a unique Request ID returned to the client.

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
  "datastream": {
    "elk_url":  "https://your-elk:9200",
    "token":    "<api-key-encoded-value>",
    "username": "",
    "password": "",
    "index":    "cribl-onboarding-requests",
    "skip_ssl": false
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

### Build

```bash
docker build -t cribl-portal .
```

### Run

```bash
docker run -d --name cribl-portal \
  -p 9229:9229 \
  -v $(pwd)/config.json:/app/config.json:ro \
  cribl-portal
```

### Useful commands

```bash
# Logs
docker logs -f cribl-portal

# Stop and remove
docker stop cribl-portal && docker rm cribl-portal

# Rebuild after code changes
docker stop cribl-portal && docker rm cribl-portal
docker build -t cribl-portal . && docker run -d --name cribl-portal \
  -p 9229:9229 \
  -v $(pwd)/config.json:/app/config.json:ro \
  cribl-portal
```

---

## Elasticsearch — Role and API Key Setup

Run these in **Kibana → Management → Dev Tools**.

### Step 1 — Create the writer role

```json
PUT _security/role/cribl_portal_writer
{
  "cluster": ["monitor"],
  "indices": [
    {
      "names": ["cribl-onboarding-requests"],
      "privileges": ["create_doc", "auto_configure", "create_index"]
    }
  ]
}
```

### Step 2 — Create the API key

```json
POST _security/api_key
{
  "name": "cribl-portal-ingest",
  "role_descriptors": {
    "cribl_portal_writer": {
      "cluster": ["monitor"],
      "indices": [
        {
          "names": ["cribl-onboarding-requests"],
          "privileges": ["create_doc", "auto_configure", "create_index"]
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
| `create_index` | Create the index/datastream on first write |
| `auto_configure` | Apply matching index templates automatically |
| `monitor` (cluster) | Required for basic health checks |

---

## Datastream document shape

Each submitted request is stored as:

```json
{
  "@timestamp":         "2024-03-20T10:00:00+00:00",
  "request_id":         "REQ-20240320-A1B2C3D4",
  "app_id":             "APP001",
  "app_name":           "payments_service",
  "region":             "azn",
  "entitlement_groups": ["group-a", "group-b"],
  "status":             "pending"
}
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FILE` | *(none)* | Path to log file — appended across runs |
