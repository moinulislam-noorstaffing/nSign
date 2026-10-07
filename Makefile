.DEFAULT_GOAL := help
COMPOSE := docker compose
COMPOSE_LOCAL := docker compose -f docker-compose.yml -f docker-compose.local.yml

help: ## Show targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.PHONY: up down clean build rebuild restart logs logs-all ps shell exec health prune test env-check

# ═══════════════════════════════════════════════════════════════════════════
# PRIMARY COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

up: ## Build and start all services with local overrides
	@$(COMPOSE_LOCAL) up -d --build
	@printf "\n  Studio      http://localhost:$$(grep ^STUDIO_PORT .env 2>/dev/null | cut -d= -f2 || echo 3000)\n"
	@printf "  ONLYOFFICE  http://localhost:$$(grep ^ONLYOFFICE_PORT .env 2>/dev/null | cut -d= -f2 || echo 8090)\n"
	@printf "  Caddy       http://localhost:8900\n\n"

down: ## Stop all services
	@$(COMPOSE_LOCAL) down

restart: ## Restart all services
	@$(COMPOSE_LOCAL) restart
	@echo "Services restarted."

# ═══════════════════════════════════════════════════════════════════════════
# BUILD COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

build: ## Build images without starting
	@$(COMPOSE_LOCAL) build

rebuild: ## Full rebuild with no cache
	@$(COMPOSE_LOCAL) build --no-cache

# ═══════════════════════════════════════════════════════════════════════════
# CLEANUP COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

clean: ## Stop and delete all data volumes
	@$(COMPOSE_LOCAL) down -v
	@echo "All data volumes removed."

prune: ## Clean up unused Docker resources (images, containers, networks)
	@docker system prune -f
	@echo "Docker resources pruned."

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING & STATUS COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

logs: ## Follow logs from specific service (s=service name, default=all)
	@$(COMPOSE_LOCAL) logs -f $(s)

logs-all: ## Follow logs from all services
	@$(COMPOSE_LOCAL) logs -f

ps: ## Show service status
	@$(COMPOSE_LOCAL) ps

health: ## Check health of all services
	@echo "Checking service health..."
	@$(COMPOSE_LOCAL) ps --format "table {{.Service}}\t{{.Status}}"

# ═══════════════════════════════════════════════════════════════════════════
# CONTAINER INTERACTION
# ═══════════════════════════════════════════════════════════════════════════

shell: ## Open shell in a container (s=service name, default=api)
	@$(COMPOSE_LOCAL) exec $(s) /bin/bash

exec: ## Execute command in container (s=service, c=command)
	@$(COMPOSE_LOCAL) exec $(s) $(c)

# ═══════════════════════════════════════════════════════════════════════════
# VERIFICATION & TESTING
# ═══════════════════════════════════════════════════════════════════════════

env-check: ## Verify environment setup and .gitignore
	@echo "✓ Checking environment..."
	@test -f .env || { echo "❌ .env file not found. Create from .env.example"; exit 1; }
	@git check-ignore -q .env && echo "✓ .env is properly ignored" || echo "⚠️  .env is not ignored"
	@git check-ignore -q docker-compose.local.yml && echo "✓ docker-compose.local.yml is properly ignored" || echo "⚠️  docker-compose.local.yml is not ignored"
	@bash scripts/verify-secrets.sh

test: ## Run tests in containers (s=service, default=api)
	@$(COMPOSE_LOCAL) exec $(s) pytest -v

# ═══════════════════════════════════════════════════════════════════════════
# UTILITY COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

stop: ## Stop services without removing them
	@$(COMPOSE_LOCAL) stop

pause: ## Pause all services
	@$(COMPOSE_LOCAL) pause

unpause: ## Resume paused services
	@$(COMPOSE_LOCAL) unpause

reset: ## Full reset (down, clean volumes, remove images)
	@$(COMPOSE_LOCAL) down -v --rmi all
	@echo "Full reset complete. Run 'make up' to start fresh."

status: ## Detailed status report
	@echo "=== Docker Compose Status ==="
	@$(COMPOSE_LOCAL) ps
	@echo "\n=== Image Info ==="
	@docker image ls | grep studio || echo "No studio images found"
	@echo "\n=== Network Info ==="
	@docker network ls | grep studio || echo "No studio networks found"
