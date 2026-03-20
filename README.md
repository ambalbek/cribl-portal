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

## Serving via Apache httpd (bastion)

Docker runs on the **remote host**. Apache runs on the **bastion** and reverse-proxies to it.

```
Browser → https://bastion/portal/
          Apache ProxyPass → http://REMOTE_HOST_IP:9229/
          Docker container → Flask :9229
```

### Apache config

Add the contents of `httpd-add-to-existing.conf` inside your existing `<VirtualHost>` block:

```bash
sudo vi /etc/httpd/conf.d/your-existing.conf
# paste contents of httpd-add-to-existing.conf inside <VirtualHost>

sudo httpd -t && sudo systemctl reload httpd
```

Replace `REMOTE_HOST_IP` with the IP of the host running Docker.

| URL | What |
|---|---|
| `https://bastion/portal/` | Client onboarding request form |
| `https://bastion/health` | Health check endpoint |

### Required Apache modules

```bash
httpd -M | grep -E 'proxy|headers'
# proxy_module, proxy_http_module, headers_module must be listed
```

### Run Docker on the remote host (production)

Bind to all interfaces so the bastion can reach it — restrict access at the network/firewall level:

```bash
docker run -d --name cribl-portal --restart unless-stopped \
  -p 9229:9229 \
  -v /path/to/config.json:/app/config.json:ro \
  cribl-portal
```

---

## Elasticsearch — ILM Policy and Datastream Template

Run these in **Kibana → Management → Dev Tools** before the first document is indexed. Apply them in order.

### Step 1 — ILM policy

```json
PUT _ilm/policy/cribl-onboarding-requests-policy
{
  "policy": {
    "phases": {
      "hot": {
        "min_age": "0ms",
        "actions": {
          "rollover": {
            "max_age":            "30d",
            "max_primary_shard_size": "50gb"
          }
        }
      },
      "warm": {
        "min_age": "30d",
        "actions": {
          "shrink":   { "number_of_shards": 1 },
          "forcemerge": { "max_num_segments": 1 }
        }
      },
      "delete": {
        "min_age": "365d",
        "actions": {
          "delete": {}
        }
      }
    }
  }
}
```

| Phase | Trigger | Action |
|---|---|---|
| **Hot** | Immediately | Rollover after 30 days or 50 GB |
| **Warm** | 30 days after rollover | Shrink to 1 shard, force-merge for read efficiency |
| **Delete** | 365 days after rollover | Permanently delete the backing index |

---

### Step 2 — Component template (mappings)

```json
PUT _component_template/cribl-onboarding-requests-mappings
{
  "template": {
    "mappings": {
      "properties": {
        "@timestamp":         { "type": "date" },
        "request_id":         { "type": "keyword" },
        "app_id":             { "type": "keyword" },
        "app_name":           { "type": "keyword" },
        "region":             { "type": "keyword" },
        "entitlement_groups": { "type": "keyword" },
        "status":             { "type": "keyword" }
      }
    }
  }
}
```

All fields are `keyword` (exact match, aggregatable) except `@timestamp`. No free-text fields — every value is filterable and usable in Kibana dashboards without extra configuration.

---

### Step 3 — Index template

```json
PUT _index_template/cribl-onboarding-requests-template
{
  "index_patterns": ["cribl-onboarding-requests*"],
  "data_stream":    {},
  "composed_of":    ["cribl-onboarding-requests-mappings"],
  "priority":       500,
  "template": {
    "settings": {
      "index.lifecycle.name": "cribl-onboarding-requests-policy",
      "number_of_shards":     1,
      "number_of_replicas":   1
    }
  }
}
```

### Step 4 — Create the datastream

```json
PUT _data_stream/cribl-onboarding-requests
```

Verify everything is wired up correctly:

```json
GET _data_stream/cribl-onboarding-requests
```

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
