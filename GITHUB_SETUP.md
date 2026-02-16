# GitHub Repository Setup Guide

## 🔐 Security First: Protecting Amadeus Credentials

**Never commit API keys or secrets to Git!** We'll use environment variables and `.gitignore` to keep credentials secure.

## 📋 Step 1: Secure Your Credentials

### Option A: Environment Variables (Recommended)

Create a local environment file that won't be committed:

```bash
# Create a local env file (NOT committed)
cp env.sh .env.local
```

Edit `.env.local` with your actual credentials:
```bash
#!/usr/bin/env bash
# Amadeus credentials - KEEP PRIVATE
export AMADEUS_CLIENT_ID="your_actual_client_id_here"
export AMADEUS_CLIENT_SECRET="your_actual_secret_here"
# ... rest of settings
```

### Option B: GitHub Secrets (For CI/CD)

If you plan to use GitHub Actions:
1. Go to your repository → Settings → Secrets and variables → Actions
2. Add these secrets:
   - `AMADEUS_CLIENT_ID`
   - `AMADEUS_CLIENT_SECRET`

## 📋 Step 2: Update .gitignore

Create/update `.gitignore` to exclude sensitive files:
```bash
# Environment files
.env.local
.env
env.sh

# Cache and runtime
runs/
cache/
*.log

# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg
```

## 📋 Step 3: Create Template env.sh

Replace `env.sh` with a template (no real credentials):
```bash
#!/usr/bin/env bash

# Amadeus credentials - REPLACE WITH YOUR VALUES
export AMADEUS_CLIENT_ID="your_client_id_here"
export AMADEUS_CLIENT_SECRET="your_secret_here"

# Base host (Amadeus test/prod)
export AMADEUS_HOST="https://test.api.amadeus.com"
export AMADEUS_ENV="test"

# Throttling (seconds between API calls)
export THROTTLE_SECONDS="1.0"
export AMADEUS_THROTTLE_SECONDS="${THROTTLE_SECONDS}"

# Cache
export CACHE_TTL_SECONDS="21600"   # 6 hours
export CACHE_DIR="runs/cache"
export AMADEUS_CACHE_TTL_SECONDS="${CACHE_TTL_SECONDS}"
export AMADEUS_CACHE_DIR="${CACHE_DIR}"

# HTTP behavior
export TIMEOUT_SECONDS="60"
export RETRIES="2"
export RETRY_BACKOFF_SECONDS="1.0"

# Flight search backend selector
export FLIGHT_SEARCH_MODE="availabilities"
```

## 📋 Step 4: Initialize Git Repository

```bash
# Initialize if not already done
git init

# Add current files (excluding .gitignore patterns)
git add .

# Initial commit
git commit -m "Initial commit: Travel agent with multi-agent architecture"
```

## 📋 Step 5: Create GitHub Repository

### Method A: Using GitHub CLI (Recommended)

```bash
# Install GitHub CLI if not present
brew install gh  # macOS
# or: sudo apt install gh  # Ubuntu

# Login to GitHub (if not already)
gh auth login

# Create private repository
gh repo create travel-agent --private --source=. --remote=origin --push
```

### Method B: Manual GitHub Setup

1. Go to [GitHub](https://github.com) and click "New repository"
2. Repository name: `travel-agent`
3. Set to **Private**
4. Don't initialize with README (we have one)
5. Click "Create repository"
6. Follow the Git commands shown:

```bash
git remote add origin https://github.com/yourusername/travel-agent.git
git branch -M main
git push -u origin main
```

## 📋 Step 6: Verify Security

Check that credentials are not in the repository:

```bash
# Search for any credential references in Git history
git log --all --full-history --grep="AMADEUS_CLIENT" --source

# Check current staged files
git status

# Verify .gitignore is working
git check-ignore env.sh .env.local .env
```

## 📋 Step 7: Setup for Other Developers

Create `SETUP.md` section for credential setup:

```markdown
## 🔐 Setup for Contributors

1. Copy the environment template:
   ```bash
   cp env.sh.template env.sh
   ```

2. Edit `env.sh` with your Amadeus credentials:
   - Get credentials from [Amadeus Developer Portal](https://developers.amadeus.com/)
   - Replace placeholder values in `env.sh`

3. Source the environment:
   ```bash
   source env.sh
   ```

4. Never commit `env.sh` - it's in .gitignore
```

## 📋 Step 8: Branch Protection (Optional but Recommended)

1. Go to repository → Settings → Branches
2. Add branch protection rule for `main`
3. Require:
   - Pull request reviews
   - Status checks to pass
   - No force pushes

## 🔄 Workflow for Development

```bash
# Always work on feature branches
git checkout -b feature/new-feature

# Make changes...
git add .
git commit -m "Add new feature"

# Push and create PR
git push origin feature/new-feature
gh pr create --title "Add new feature" --body "Description of changes"
```

## 🚨 Security Checklist

- [ ] `.env.local` created with actual credentials
- [ ] `env.sh` contains only placeholders
- [ ] `.gitignore` excludes all environment files
- [ ] No credentials in Git history
- [ ] Repository is set to private
- [ ] Team members know not to commit credentials
- [ ] GitHub secrets configured for CI/CD (if applicable)

## 📞 Support

If credentials accidentally get committed:
1. Immediately revoke them at [Amadeus Developer Portal](https://developers.amadeus.com/)
2. Generate new credentials
3. Remove from Git history:
   ```bash
   git filter-branch --force --index-filter 'git rm --cached --ignore-unmatch env.sh' --prune-empty --tag-name-filter cat -- --all
   git push origin --force --all
   ```
4. Add to .gitignore and commit the fix

---

**🔐 Remember: Environment variables are your friend!**
