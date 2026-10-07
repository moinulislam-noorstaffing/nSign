#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  Pre-commit Security Verification Script
#
#  Usage:
#    bash scripts/verify-secrets.sh          # Check staged files
#    bash scripts/verify-secrets.sh --all    # Check all repo files
#
#  Run this BEFORE every commit to ensure no secrets are being pushed.
# ═══════════════════════════════════════════════════════════════════════════

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

REPO_ROOT=$(git rev-parse --show-toplevel)
ERRORS=0

echo "🔒 Security Verification"
echo "========================"
echo ""

# ─────────────────────────── Sensitive Patterns ─────────────────────────────
PATTERNS=(
    "password\s*[:=]"
    "secret\s*[:=]"
    "api[_-]?key\s*[:=]"
    "openai"
    "pandadoc"
    "sk-[A-Za-z0-9]"
    "bearer\s"
    "authorization\s*:\s*Bearer"
)

# ─────────────────────────── Check Staged Files ─────────────────────────────
if [[ "$1" != "--all" ]]; then
    echo "📋 Checking STAGED files for secrets..."
    echo ""

    # Get staged files
    STAGED=$(git diff --cached --name-only --diff-filter=ACM)

    if [[ -z "$STAGED" ]]; then
        echo "ℹ️  No staged changes to check."
        echo ""
    else
        echo "Staged files:"
        echo "$STAGED" | sed 's/^/  - /'
        echo ""

        # Check for .env files
        if echo "$STAGED" | grep -qE "\.env"; then
            echo -e "${RED}❌ ERROR: .env file is staged!${NC}"
            echo "   This file contains secrets and must NOT be committed."
            echo "   Fix: git reset .env"
            ((ERRORS++))
        fi

        # Check for docker-compose overrides
        if echo "$STAGED" | grep -qE "docker-compose\.(local|override)\.yml"; then
            echo -e "${RED}❌ ERROR: Local docker-compose file is staged!${NC}"
            echo "   This file contains local-specific settings and must NOT be committed."
            echo "   Fix: git reset docker-compose.local.yml"
            ((ERRORS++))
        fi

        # Check content for sensitive patterns
        for pattern in "${PATTERNS[@]}"; do
            if git diff --cached | grep -iE "$pattern" > /dev/null 2>&1; then
                echo -e "${YELLOW}⚠️  WARNING: Possible secret found: $pattern${NC}"
                echo "   Review with: git diff --cached | grep -iE '$pattern'"
                ((ERRORS++))
            fi
        done
    fi

# ─────────────────────────── Check All Files (--all) ─────────────────────────
else
    echo "📋 Checking ALL repo files for secrets (slow)..."
    echo ""

    # Find files that shouldn't exist
    echo "Checking for .env files in repo..."
    if find "$REPO_ROOT" -name ".env*" -type f ! -name ".env.example" | grep -q .; then
        echo -e "${RED}❌ ERROR: Found .env files in repo!${NC}"
        find "$REPO_ROOT" -name ".env*" -type f ! -name ".env.example" | sed 's/^/   - /'
        ((ERRORS++))
    fi

    echo "Checking for secret patterns in tracked files..."
    if git grep -iE "password\s*[:=]|secret\s*[:=]|api[_-]?key\s*[:=]" -- ':!' '.env.example' > /dev/null 2>&1; then
        echo -e "${RED}❌ ERROR: Possible secrets found in tracked files!${NC}"
        git grep -iE "password\s*[:=]|secret\s*[:=]|api[_-]?key\s*[:=]" -- ':!' '.env.example' | head -5
        echo "   Review all matches:"
        git grep -iE "password\s*[:=]|secret\s*[:=]|api[_-]?key\s*[:=]" -- ':!' '.env.example'
        ((ERRORS++))
    fi
fi

# ─────────────────────────── Verify Gitignore ─────────────────────────────
echo ""
echo "📝 Verifying .gitignore configuration..."
echo ""

# Check if .env is properly ignored
if git check-ignore -q .env 2>/dev/null; then
    echo -e "${GREEN}✅ .env is properly ignored${NC}"
else
    echo -e "${RED}❌ .env is NOT ignored by git!${NC}"
    ((ERRORS++))
fi

# Check if docker-compose.local.yml is properly ignored
if git check-ignore -q docker-compose.local.yml 2>/dev/null; then
    echo -e "${GREEN}✅ docker-compose.local.yml is properly ignored${NC}"
else
    echo -e "${YELLOW}⚠️  docker-compose.local.yml is NOT ignored (but should be)${NC}"
fi

# ─────────────────────────── Summary ─────────────────────────────
echo ""
echo "════════════════════════════════════════"
if [[ $ERRORS -eq 0 ]]; then
    echo -e "${GREEN}✅ All security checks passed!${NC}"
    echo "Safe to push to GitHub."
    exit 0
else
    echo -e "${RED}❌ $ERRORS security issue(s) found!${NC}"
    echo ""
    echo "DO NOT PUSH until these are fixed:"
    echo "  1. Run: git status"
    echo "  2. Unstage secret files: git reset <file>"
    echo "  3. Check .gitignore: cat .gitignore"
    echo ""
    echo "For help, see: SECURITY.md"
    exit 1
fi
