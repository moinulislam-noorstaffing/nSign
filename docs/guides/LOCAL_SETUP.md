# Local Development Setup

Complete guide to setting up Offer Letter Studio for local development.

## Prerequisites

- **Docker & Docker Compose** (v2.0+): [Install Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Git**: For cloning the repository
- **Make** (optional): For convenient command shortcuts

## Quick Start (5 minutes)

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd nSign
```

### 2. Verify Environment Setup

```bash
# Run security and environment checks
make env-check
```

This will:

- ✓ Verify `.env` file exists
- ✓ Check `.gitignore` configuration
- ✓ Scan for accidental secrets

### 3. Create Your Local Environment File

```bash
# Copy the example file
cp .env.example .env
```

Edit `.env` with your local values:

```bash
# For local development, these defaults are fine:
POSTGRES_PASSWORD=studio
ONLYOFFICE_JWT_SECRET=dev-secret-key
S3_SECRET_KEY=studio

# Add real API keys only if you need these features:
# OPENAI_API_KEY=sk-your-key-here
# PANDADOC_API_KEY=your-key-here
```

### 4. Start the Stack

**Using Make (recommended):**

```bash
make up
```

**Or with docker-compose directly:**

```bash
docker compose -f docker-compose.yml -d
```

### 5. Verify Services are Running

```bash
# Quick status check
make health

# Detailed status
make ps
```

All services should show `healthy` or `running` status.

### 6. Access the Application

- **Studio UI**: http://localhost:8900
- **ONLYOFFICE Editor**: http://localhost:8090
- **MinIO Console**: not published to the host by default — see [Storage/MinIO Issues](#storageminio-issues)
- **API Docs**: http://localhost:8000/api/docs
- **API Health**: http://localhost:8000/api/health

## Make Commands Reference

Essential commands for development:

```bash
# Service Management
make up           # Start all services with local overrides
make down         # Stop all services
make restart      # Restart services
make stop         # Stop without removing containers

# Logging
make logs         # Follow logs from all services
make logs s=api   # Follow logs from specific service
make health       # Check service health status
make ps           # Show service status

# Building & Cleanup
make build        # Build images without starting
make rebuild      # Full rebuild with no cache
make clean        # Stop and delete all data volumes
make prune        # Clean up unused Docker resources
make reset        # Full reset (remove everything)

# Development
make shell s=api  # Open bash shell in API container
make shell s=web  # Open bash shell in web container
make exec s=api c="pytest -v"  # Run tests

# Verification
make env-check    # Verify environment & .gitignore
make status       # Detailed system status
```

See all available commands:

```bash
make help
```

## Environment Variables

All variables are documented in `.env.example`. Here are the key ones:

### Critical Secrets (Required for Development)

| Variable                | Purpose                 | Default          |
| ----------------------- | ----------------------- | ---------------- |
| `POSTGRES_PASSWORD`     | Database password       | `studio`         |
| `ONLYOFFICE_JWT_SECRET` | Document server signing | `dev-secret-key` |
| `S3_SECRET_KEY`         | MinIO storage access    | `studio`         |

### Configuration (Ports & URLs)

| Variable          | Purpose       | Default    |
| ----------------- | ------------- | ---------- |
| `STUDIO_PORT`     | Web UI port   | `8900`     |
| `ONLYOFFICE_PORT` | Editor port   | `8090`     |
| `POSTGRES_HOST`   | Database host | `postgres` |
| `MINIO_HOST`      | Storage host  | `minio`    |

### Optional (For Features)

| Variable         | Purpose                                        | When Needed          |
| ---------------- | ---------------------------------------------- | -------------------- |
| `OPENAI_API_KEY` | AI features (analysis, composition, discovery) | If using AI features |

**Never hardcode secrets.** Use `.env` (gitignored) for local development only.

## Hot Reload Setup (Advanced)

Enable code changes to rebuild without restarting containers:

### 2. Restart services

```bash
make restart
# or
docker compose up -d
```

### 3. Watch for changes

- **Backend**: FastAPI auto-reloads on code changes
- **Frontend**: Next.js hot-reloads in browser
- Changes to `package.json` or `requirements.txt` require rebuild

**Note:** Volume mounts may impact performance on some systems (especially Mac with Docker Desktop).

## Troubleshooting

### Services Won't Start

**Check service health:**

```bash
make health
```

**View detailed logs:**

```bash
make logs s=<service>  # service: postgres, api, web, onlyoffice, etc.
make logs-all          # all services
```

### Port Conflicts

If ports 8900 or 8090 are already in use:

```bash
# Edit .env to change ports:
STUDIO_PORT=3001        # Changed from 8900
ONLYOFFICE_PORT=8091    # Changed from 8090

# Restart services
make restart
```

### Memory/CPU Issues

If Docker is using too many resources:

```bash
# Check current usage
docker stats

# Reduce memory limits in docker-compose.local.yml:
postgres:
  mem_limit: 500m   # Reduced from 1g

gotenberg:
  mem_limit: 1g     # Reduced from 1.5g

# Restart
make restart
```

### ONLYOFFICE Takes Forever to Start

- **First start:** 120-180 seconds (compiles documents)
- **Check status:** `docker compose logs onlyoffice`
- **Wait longer:** Give it 2-3 minutes on first run
- **Reduce memory:** May help on slow machines
- **Try reset:** `make clean && make up`

### Database Connection Fails

```bash
# Check PostgreSQL logs
make logs s=postgres

# Verify password is set
grep POSTGRES_PASSWORD .env

# Reset database completely
make clean
make up

# Or connect manually to troubleshoot
docker compose exec postgres psql -U studio -d studio -c "\dt"
```

### API Returns 502 Bad Gateway

```bash
# ONLYOFFICE might still be starting
make logs s=onlyoffice

# Wait 2-3 minutes and refresh the browser
# Or restart just the API
docker compose restart api

# Check API health directly
curl http://localhost:8000/api/health
```

### .env File Issues

```bash
# Verify .env exists and is ignored
make env-check

# Recreate from example
cp .env.example .env

# Ensure it has required variables
grep POSTGRES_PASSWORD .env
grep ONLYOFFICE_JWT_SECRET .env
```

### Storage/MinIO Issues

```bash
# MinIO has no host port mapping by default. Browse it from inside the container:
docker compose exec minio sh -c 'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc ls local'

# Browser console: publish 9001 in docker-compose.local.yml, then open
# http://localhost:9001 and log in with S3_ACCESS_KEY / S3_SECRET_KEY from .env
# (not minioadmin).

# Check storage logs
make logs s=minio


### API Routes

- **Health Check**: `GET http://localhost:8000/api/health`
- **API Docs**: `http://localhost:8000/api/docs` (Swagger UI)
- **ReDoc**: `http://localhost:8000/api/redoc`

### File Locations

- **Backend Code**: `backend/app/`
- **Frontend Code**: `web/app/`, `web/components/`, `web/lib/`
- **Database Migrations**: `backend/migrations/`
- **Docker Config**: `docker-compose.yml`, `docker-compose.local.yml`
- **Local Data**: `data/` directory (gitignored)


## Quick Reference

| Task             | Command                  |
| ---------------- | ------------------------ |
| Start everything | `make up`                |
| Stop everything  | `make down`              |
| View logs        | `make logs s=<service>`  |
| Get shell access | `make shell s=<service>` |
| Run tests        | `make test`              |
| Check health     | `make health`            |
| Full cleanup     | `make reset`             |
```
