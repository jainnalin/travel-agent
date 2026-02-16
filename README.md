# Travel Agent

🧠 **Multi-agent travel planning system** powered by LLM intelligence and real-time Amadeus API integration.

## 🎯 Overview

Travel Agent is a sophisticated AI-powered travel planning platform that uses natural language processing to understand user queries and orchestrates multiple specialized agents to find optimal travel options. The system combines hotel searches, flight bookings, and intelligent ranking to deliver comprehensive travel plans.

### ✨ Key Features

- 🗣️ **Natural Language Processing**: Understand complex travel queries in plain English
- 🏨 **Hotel Search**: Real-time hotel availability and pricing via Amadeus API
- ✈️ **Flight Search**: Comprehensive flight options with return journey support
- 🎒 **Bundle Planning**: Combined hotel + flight itineraries
- 🤖 **LLM Intelligence**: Mistral-powered query understanding and planning
- 🔄 **Adaptive Replanning**: Intelligent retry logic with result optimization
- 📊 **Parallel Execution**: DAG-based orchestration for optimal performance
- 💰 **Budget Awareness**: Cost constraints and preference filtering
- 🌍 **Airport Resolution**: Smart city-to-IATA code mapping with prioritization

## 🏗️ Architecture

The system follows a **multi-agent orchestration pattern** with clear separation of concerns:

```
User Intent → Planner → Parallel Execution → Evaluation → Decision Loop
```

### Active Agents (9 Total)

| Agent | Purpose |
|--------|---------|
| **LLMPlannerAgent** | Generates execution plans from natural language queries |
| **HotelSearchAgent** | Searches hotels via Amadeus API with filters |
| **FlightSearchAgent** | Searches flights with return journey support |
| **BundleComposeAgent** | Combines hotel + flight results into packages |
| **SimpleRankerAgent** | Ranks results by relevance and user preferences |
| **ValidatorAgent** | Validates result quality and completeness |
| **CostGuardAgent** | Enforces budget constraints and usage limits |
| **GeoAgent** | Geographic enrichment and location services |
| **EnrichAgent** | Contextual data enhancement and preference scoring |

### Execution Flow

1. **Intent Parsing** → Natural language → structured travel parameters
2. **Plan Generation** → LLM creates DAG of execution steps
3. **Parallel Execution** → Independent steps run simultaneously
4. **Result Enrichment** → Contextual data and preference scoring
5. **Adaptive Evaluation** → Quality assessment and replanning decisions
6. **Final Ranking** → Optimized results presentation

## 📁 Project Structure

| File/Folder | Purpose |
|--------------|---------|
| **`pyproject.toml`** | Project metadata, dependencies, and build configuration |
| **`env.sh`** | Environment variables (Amadeus credentials, API settings) |
| **`main.py`** | CLI entry point with automatic environment sourcing |
| **`setup.sh`** | Automated setup script for dependencies and services |
| **`SETUP.md`** | Detailed setup and configuration guide |
| **`travel_agent/`** | Main package directory |
| **`travel_agent/cli/`** | Command-line interface and query processing |
| **`travel_agent/nlp/`** | Natural language parsing and intent extraction |
| **`travel_agent/agents/`** | Specialized agents for different tasks |
| **`travel_agent/agents/planner/`** | LLM-based planning logic |
| **`travel_agent/agents/executors/`** | Search agents (hotels, flights, bundles) |
| **`travel_agent/agents/critics/`** | Validation, ranking, and cost control |
| **`travel_agent/agents/support/`** | Utility agents (geo, enrichment, explanation) |
| **`travel_agent/orchestrator/`** | DAG execution engine and loop management |
| **`travel_agent/tools/geo/`** | Geographic data and airport resolution |
| **`travel_agent/contracts/`** | Data models and interface definitions |
| **`tests/`** | Test suite (placeholder for future enhancement) |
| **`runs/`** | Runtime cache and execution logs |
| **`docs/`** | Documentation (placeholder for future enhancement) |

## 🚀 Quick Start

### Prerequisites

- Python 3.9+
- Ollama (for LLM services)
- Amadeus API credentials

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd travel-agent

# Automated setup (recommended)
./setup.sh

# Or manual setup
python3 -m venv venv
source venv/bin/activate
pip install -e .
source env.sh
```

## 💻 Usage

### Hotel-Only Search

Find hotels with specific dates and preferences:

```bash
python3 main.py "hotels in Boston for 3 days starting July 1st"
```

**Output**: Hotel options with per-day pricing, ratings, and location details

### Flight-Only Search

Search for flights with return journey support:

```bash
python3 main.py "flights from Dallas to Boston on July 1st returning July 5th"
```

**Output**: Flight options with times, prices, and return journey details

### Bundle Travel Plans

Complete travel packages with hotels and flights:

```bash
python3 main.py "provide travel plan for 4 days from Dallas to New York on July 1st"
```

**Output**: Combined hotel + flight options with total pricing and scheduling

### Advanced Queries

The system understands complex natural language:

```bash
# Budget constraints
python3 main.py "find hotels in Miami under $200 per night for 2 days"

# Preference-based
python3 main.py "family-friendly hotels in Orlando with free cancellation for 5 days"

# Multi-city
python3 main.py "flights from Chicago to Denver then to Los Angeles starting June 15th"
```

## 🔧 Configuration

### Environment Variables

Key settings in `env.sh`:

| Variable | Purpose |
|-----------|---------|
| `AMADEUS_CLIENT_ID` | Amadeus API client identifier |
| `AMADEUS_CLIENT_SECRET` | Amadeus API secret key |
| `AMADEUS_HOST` | API endpoint (test/prod) |
| `THROTTLE_SECONDS` | Rate limiting between API calls |
| `CACHE_TTL_SECONDS` | Cache duration for results |
| `TIMEOUT_SECONDS` | Request timeout settings |

### LLM Configuration

- **Model**: Mistral (default, configurable)
- **Service**: Ollama on `localhost:11434`
- **Fallback**: Mock planner for reliability

## 🧪 Development

### Running Tests

```bash
# Install development dependencies
pip install -e .[dev]

# Run test suite
pytest

# With coverage
pytest --cov=travel_agent --cov-report=html
```

### Code Quality

```bash
# Format code
black travel_agent/

# Type checking
mypy travel_agent/

# Linting
flake8 travel_agent/
```

## 📊 Performance Features

### Parallel Execution

- **DAG-based orchestration** with dependency resolution
- **ThreadPoolExecutor** with configurable workers (default: 6)
- **Timeout handling** prevents blocking operations
- **Resource limits** and budget enforcement

### Adaptive Replanning

- **Result-based optimization** with automatic retry logic
- **Search expansion** when insufficient results found
- **Mode switching** between flight search types
- **Timeout adaptation** for problematic steps

### Smart Caching

- **6-hour cache TTL** for API responses
- **Deduplication** to reduce redundant calls
- **Persistent storage** in `runs/cache/`

## 🔍 API Integration

### Amadeus Travel APIs

- **Hotel Search**: Real-time availability and pricing
- **Flight Search**: Offers and availabilities modes
- **Airport Data**: Comprehensive airport database
- **Geographic Resolution**: City-to-IATA mapping

### Data Processing

- **Airport Prioritization**: Large > Medium > Small airports
- **Longest Match Algorithm**: Prevents partial city name matches
- **Date Normalization**: Flexible date parsing with dateparser
- **Price Per Day**: Automatic calculation for multi-day stays

## 🛣️ Roadmap

### Planned Enhancements

- [ ] **Abstract Query Processing**: "cooler place", "warmer destination"
- [ ] **Preference Extraction**: LLM-driven natural language preferences
- [ ] **Real-time Adaptation**: Dynamic DAG modification during execution
- [ ] **Multi-city Support**: Complex itinerary planning
- [ ] **Budget Optimization**: Cost-aware search and ranking
- [ ] **Alternative Suggestions**: When primary searches fail

### Future File Structure

- `tests/` - Comprehensive test suite
- `docs/` - API documentation and guides
- `travel_agent/agents/preferences/` - Preference extraction agents
- `travel_agent/agents/adapters/` - Alternative API integrations

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Amadeus** - Travel API and data services
- **Ollama** - LLM inference engine
- **Mistral AI** - Natural language understanding model

---

**🚀 Ready to plan your next adventure?** 

```bash
python3 main.py "your travel query here"
```
