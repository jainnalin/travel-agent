# Travel Agent Setup Guide

## 🚀 Quick Setup

### Option 1: Automated Setup (Recommended)

```bash
# Clone and setup
git clone <repository>
cd travel-agent
./setup.sh
```

The setup script will:
- ✅ Create virtual environment
- ✅ Install all dependencies from `pyproject.toml`
- ✅ Check/start Ollama service
- ✅ Pull Mistral model
- ✅ Load environment variables from `env.sh`
- ✅ Verify Amadeus credentials
- ✅ Create cache directories

### Option 2: Manual Setup

```bash
# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -e .

# 3. Start Ollama (if not running)
ollama serve &
ollama pull mistral

# 4. Load environment
source env.sh
```

## 📋 Dependencies

The project uses modern Python packaging with `pyproject.toml` as the single source of truth:

### Core Dependencies:
- **fastapi>=0.100.0** - Web framework
- **uvicorn[standard]>=0.20.0** - ASGI server
- **pydantic>=2.0.0** - Data validation
- **tabulate>=0.9.0** - Table formatting
- **requests>=2.31.0** - HTTP client
- **ollama>=0.1.0** - LLM integration
- **dateparser>=1.2.0** - Date parsing
- **python-dateutil>=2.8.0** - Date utilities

### Development Dependencies (optional):
- **pytest>=7.0.0** - Testing framework
- **pytest-cov>=4.0.0** - Coverage reporting
- **black>=23.0.0** - Code formatting
- **flake8>=6.0.0** - Linting
- **mypy>=1.0.0** - Type checking

Install with: `pip install -e .[dev]`

## 🔐 Environment Setup

The `env.sh` file contains:
- Amadeus API credentials
- Cache settings
- Throttling configuration
- HTTP behavior settings

The `main.py` automatically sources `env.sh` when run, so you don't need to manually source it each time.

## 🏃‍♂️ Running the Agent

### Using main.py (auto-sources env.sh):
```bash
python3 main.py "hotels in Boston for 3 days starting July 1st"
python3 main.py "flights from Dallas to Boston on July 1st"
python3 main.py "provide travel plan for 4 days from Dallas to New York on July 1st"
```

### Using CLI script:
```bash
travel-agent "hotels in Boston for 3 days"
```

### Debug mode:
```bash
python3 main.py --debug "your query here"
```

## 🤖 LLM Requirements

- **Ollama**: Must be running on `localhost:11434`
- **Mistral model**: Automatically pulled by setup script
- **Alternative models**: Can be configured in `llm_based.py`

## ✅ Verification

After setup, verify everything works:

```bash
# Test hotels
python3 main.py "hotels in Boston for 2 days"

# Test flights  
python3 main.py "flights from Dallas to Boston"

# Test bundle
python3 main.py "provide travel plan for 4 days from Dallas to New York"
```

## 🔧 Troubleshooting

### Ollama Issues
```bash
# Check if running
curl http://localhost:11434/api/tags

# Restart if needed
pkill ollama
ollama serve &
```

### Environment Issues
```bash
# Check credentials
echo $AMADEUS_CLIENT_ID
echo $AMADEUS_CLIENT_SECRET

# Re-source environment
source env.sh
```

### Dependencies
```bash
# Reinstall if needed
pip install -e .
```

## 📁 Project Structure

```
travel-agent/
├── pyproject.toml     # Dependencies, metadata, and build config
├── env.sh            # Environment variables (API keys, settings)
├── setup.sh          # Automated setup script
├── main.py           # Auto-sources env.sh, CLI entry point
└── SETUP.md          # This guide
```

**Note**: `requirements.txt` has been removed - `pyproject.toml` is now the single source of truth for all dependencies and project configuration.
