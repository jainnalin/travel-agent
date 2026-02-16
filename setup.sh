#!/usr/bin/env bash

# Travel Agent Setup Script
# Ensures all dependencies are installed and environment is configured

set -e

echo "🚀 Setting up Travel Agent..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies from pyproject.toml
echo "📚 Installing dependencies from pyproject.toml..."
pip install -e .

# Check if Ollama is running
echo "🤖 Checking Ollama..."
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "⚠️  Ollama is not running. Starting Ollama..."
    if command -v ollama &> /dev/null; then
        ollama serve &
        sleep 5
        echo "✅ Ollama started"
    else
        echo "❌ Ollama not found. Please install Ollama first:"
        echo "   curl -fsSL https://ollama.ai/install.sh | sh"
        exit 1
    fi
else
    echo "✅ Ollama is running"
fi

# Check if mistral model is available
echo "🧠 Checking Mistral model..."
if ! ollama list | grep -q "mistral"; then
    echo "📥 Pulling Mistral model..."
    ollama pull mistral
else
    echo "✅ Mistral model is available"
fi

# Source environment variables
echo "🔐 Loading environment variables..."
if [ -f "env.sh" ]; then
    source env.sh
    echo "✅ Environment variables loaded from env.sh"
else
    echo "❌ env.sh file not found!"
    exit 1
fi

# Verify Amadeus credentials
echo "🔑 Verifying Amadeus credentials..."
if [ -z "$AMADEUS_CLIENT_ID" ] || [ -z "$AMADEUS_CLIENT_SECRET" ]; then
    echo "❌ Amadeus credentials not found in environment!"
    exit 1
else
    echo "✅ Amadeus credentials loaded"
fi

# Create cache directory
echo "💾 Creating cache directory..."
mkdir -p runs/cache

echo ""
echo "🎉 Setup complete! You can now run:"
echo "   source venv/bin/activate && source env.sh"
echo "   python3 main.py \"hotels in Boston for 3 days starting July 1st\""
echo ""
echo "Or use the CLI directly:"
echo "   travel-agent \"flights from Dallas to Boston on July 1st\""
