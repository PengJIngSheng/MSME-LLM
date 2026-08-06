# bisnes.ai Workspace Assistant

An AI workspace assistant that combines local LLM serving, agentic document workflows,
retrieval-augmented generation, web research, file generation, user memory, and Google
Workspace automation in one FastAPI application.

This project is written as a portfolio-grade AI engineering system: it is not a simple
chatbot wrapper, but a multi-workflow assistant with persistent storage, streaming
responses, configurable model backends, document agents, structured-data analysis,
Google API integrations, and a browser-based user interface.

## Why This Project Matters

Modern AI products are rarely just one model call. A production-facing assistant needs
to decide when to answer directly, when to retrieve knowledge, when to search the web,
when to ask for confirmation, when to generate files, and how to persist user context
without exposing secrets or breaking deployment environments.

bisnes.ai demonstrates those engineering concerns in a single codebase:

- A FastAPI backend that streams model responses with Server-Sent Events
- Agent routing for chat, web research, PDFs, structured financial files, and workspace actions
- Local model support through Ollama and configurable model profiles
- MongoDB/GridFS storage for users, chat history, uploads, generated files, and credentials
- PostgreSQL + PGVector support for long-term memory, internal knowledge retrieval, and web cache embeddings
- Google Workspace connectors for Drive, Gmail, Docs, Sheets, Slides, Calendar, and Meet
- Local artifact generation for PDF, DOCX, PPTX, and XLSX outputs
- Multi-environment configuration for local Windows development and Ubuntu server deployment

## Hiring-Signal Summary

From an AI development company or technical recruiter perspective, this repository
shows experience across the parts of an applied AI product that matter in practice:

| Area | Evidence in This Repository |
| --- | --- |
| Backend engineering | FastAPI app, auth routes, streaming chat pipeline, API endpoints, file handling |
| LLM integration | Ollama model runtime, configurable model selection, prompt routing, context injection |
| Agentic workflows | PDF Agent, Financial Data Agent, Google Workspace Agent, file-generation skills |
| RAG and memory | PGVector-backed long-term memory, internal knowledge retrieval, web cache retrieval |
| Document AI | PDF extraction, table parsing, report generation, DOCX/PDF/PPTX/XLSX creation |
| Data engineering | CSV, Excel, JSON, JSONL, TXT, Markdown analysis for business and financial files |
| Product thinking | Authentication, chat history, feedback, source cards, confirmation flow for Gmail |
| DevOps readiness | Profile-based configuration, secrets separation, local/server deployment profiles |
| Testing discipline | Unit tests for web-search decision logic, parsing, scoring, and mocked researcher flow |

## Core Features

### Conversational AI

- Streaming chat responses through FastAPI and SSE
- User identity, chat history, and feedback persistence
- Optional injection of long-term memory and internal knowledge context
- Support for multilingual business use cases, including English, Chinese, and Malay routing logic

### Web Research Mode

- Detects when a user query needs current information
- Searches external sources through configurable providers
- Fetches and cleans web page content
- Scores and chunks retrieved content
- Returns source cards to the frontend for traceability
- Supports cached retrieval through PGVector when enabled

### PDF Agent

- Extracts text and tables from uploaded PDFs
- Maintains a workflow state machine for multi-step PDF tasks
- Supports template-aware report generation
- Stores uploaded PDFs and generated reports in GridFS
- Produces downloadable PDF outputs

### Financial Data Agent

- Handles CSV, Excel, JSON, JSONL, TXT, and Markdown files
- Extracts useful fields and summarizes structured business data
- Identifies missing information, patterns, and recommendations
- Designed for practical MSME and business-analysis workflows

### Local File Generation

The assistant can generate local business artifacts from user requests:

- PDF reports
- Word documents
- PowerPoint presentations
- Excel workbooks

Generated files are stored in GridFS and returned to the frontend as downloadable
file cards.

### Google Workspace Automation

Supported Google services include:

- Google Drive
- Gmail
- Google Docs
- Google Sheets
- Google Slides
- Google Calendar
- Google Meet

Gmail uses a safer confirmation flow: the system prepares a draft or preview first,
and the user must confirm before any email is sent.

### Long-Term Memory and Knowledge Retrieval

The assistant supports two retrieval layers:

- Long-term user memory for personalized context
- Internal knowledge-base retrieval for MSME and business-domain knowledge

Both are designed around PGVector-backed embeddings and configurable connection
settings.

## Architecture

```text
Browser UI
  |
  | HTTP + Server-Sent Events
  v
FastAPI Backend
  |
  |-- Auth and account APIs
  |-- Chat router and streaming pipeline
  |-- Web Research module
  |-- PDF Agent
  |-- Financial Data Agent
  |-- Google Workspace Agent
  |-- Memory and Knowledge RAG
  |-- Local file-generation skills
  |
  |-- MongoDB + GridFS
  |     |-- Users
  |     |-- Chats
  |     |-- Feedback
  |     |-- Uploads
  |     |-- Generated artifacts
  |     `-- Connector credentials
  |
  |-- PostgreSQL + PGVector
  |     |-- Long-term memory
  |     |-- Internal knowledge retrieval
  |     `-- Web content cache
  |
  `-- Ollama / Local Model Runtime
```

## Request Flow

```text
User message or upload
  |
  v
Frontend sends request to FastAPI
  |
  v
Backend loads user, chat, files, profile, and mode settings
  |
  v
Router selects workflow:
  - Normal chat
  - Web research
  - PDF Agent
  - Financial Data Agent
  - Google Workspace Agent
  - Local file generation
  - Image analysis or generation
  |
  v
Model context is assembled with memory, RAG, web results, or file content
  |
  v
Local model or configured backend generates response
  |
  v
Response streams to browser; files and metadata are persisted
```

## Technology Stack

- Python
- FastAPI
- Uvicorn
- MongoDB and GridFS
- PostgreSQL with PGVector
- Ollama
- LangChain / LangChain PGVector
- Playwright
- PyMuPDF and pdfplumber
- pandas and tabulate
- python-docx, python-pptx, openpyxl, reportlab
- Google API client libraries
- Vanilla HTML, CSS, and JavaScript

## Project Structure

```text
.
|-- server.py                         # FastAPI app and main chat pipeline
|-- Model_StartUp.py                  # Local model loading and interactive runner
|-- config_loader.py                  # Runtime configuration loader
|-- dev.py                            # Development runner with reload polling
|-- USER_MANUAL.md                    # User-facing feature guide
|-- config/
|   |-- default.yaml                  # Shared defaults
|   |-- local.windows.yaml            # Local Windows profile
|   |-- server.ubuntu.yaml            # Ubuntu server profile
|   |-- README.md                     # Configuration notes
|   `-- secrets.example.env           # Example secrets file
|-- static/
|   |-- index.html                    # Main web UI
|   |-- script.js                     # Frontend app logic
|   |-- style.css                     # UI styles
|   `-- locales/                      # English, Chinese, and Malay UI text
|-- interface functions/
|   `-- auth.py                       # Authentication and account APIs
|-- AI agent/
|   |-- PDF Agent/                    # PDF workflow and PDF generation
|   |-- Financial Data Agent/         # Structured financial data analysis
|   |-- google_agent.py               # Google Workspace intent router
|   |-- google_workspace_tools.py     # Google Workspace API tools
|   |-- memory_agent.py               # Long-term user memory
|   |-- knowledge_agent.py            # Internal knowledge RAG
|   `-- skill_impl/                   # DOCX/PDF/PPTX/XLSX file generation
|-- Model Networking/
|   `-- web_search.py                 # Web research implementation
|-- text_utils.py                     # Script-aware message sizing (EN/ZH/MS)
|-- ImageGemma4/                      # Optional image modules
|-- scripts/                          # RAG ingestion and fine-tuning utilities
|-- tests/                            # Unit tests
|-- data/                             # Local data and training materials
`-- models/                           # Local model files
```

## Configuration

Runtime settings are managed by `config_loader.py` so the same application can run
on a local development machine or a server by switching profiles.

Configuration merge order:

1. Built-in defaults
2. `config/default.yaml`
3. `config/<APP_PROFILE>.yaml`
4. `CONFIG_FILE`, if set
5. Environment variables and secrets files

Default profile:

```text
local.windows
```

Run with the local Windows profile:

```powershell
$env:APP_PROFILE = "local.windows"
python server.py
```

Run with the Ubuntu server profile:

```bash
export APP_PROFILE=server.ubuntu
python server.py
```

`MOF_PROFILE` is also accepted as an alternative to `APP_PROFILE`.

## Secrets

Secrets should not be committed to the repository. Use environment variables or
profile-specific secrets files:

```text
config/secrets.local.windows.env
config/secrets.server.ubuntu.env
```

Common secret and service values:

- `SMTP_APP_PASSWORD`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `GOOGLE_CLIENT_SECRET_FILE`
- `JWT_SECRET`
- `BRAVE_SEARCH_API_KEY`
- `TAVILY_API_KEY`
- `PGVECTOR_CONNECTION_URI`
- `OLLAMA_HOST`

## Local Development

Install dependencies:

```bash
pip install -r requirements.txt
```

Install Playwright browser dependencies if PDF rendering or browser-based document
generation is needed:

```bash
playwright install
```

Start the backend:

```bash
python server.py
```

The app is usually available at:

```text
http://localhost:8000
```

For development with automatic Python-file reload polling:

```bash
python dev.py  # http://127.0.0.1:8001 (keeps the production port 8000 free)
```

## Model Runtime

The backend primarily uses Ollama for local model serving. Active model names,
generation limits, and runtime endpoints are controlled by configuration.

Important model settings:

- `model.think_model`
- `model.fast_model`
- `model.search_query_model`
- `model.gguf_path`
- `generation.max_new_tokens`
- `generation.temperature`
- `services.ollama.base_url`
- `services.ollama.num_ctx_cap`

## Databases

### MongoDB

MongoDB stores:

- Users and account data
- Pending OTPs
- Chat history
- Feedback
- Google connector credentials
- Uploaded files through GridFS
- Generated PDFs, images, and document artifacts through GridFS

### PostgreSQL + PGVector

PGVector stores:

- Long-term user memory
- Internal knowledge-base embeddings
- Web content cache embeddings

## Scripts

Useful scripts:

```bash
python scripts/init_web_cache.py
python scripts/ingest_finetune_rag.py
python scripts/prepare_gemma4_finetune_data.py
python scripts/train_gemma4_qlora.py
```

These scripts support web-cache initialization, internal knowledge ingestion, and
Gemma-based fine-tuning data preparation/training experiments.

## Tests

Run the current unit test suite:

```bash
python -m pytest tests/test_web_search.py -v
```

The web-search tests cover:

- Language detection
- Search-intent detection
- Text chunking
- Markdown and HTML cleaning
- Result scoring
- Mocked web researcher preparation flow

## Engineering Decisions

### Local-first model runtime

The project is designed around local or privately hosted model execution through
Ollama, which makes it suitable for privacy-sensitive internal business workflows.

### Profile-based deployment

Configuration profiles separate local development from server deployment. This keeps
model paths, database URLs, service hosts, and secrets out of hardcoded application
logic.

### Agent routing instead of one generic prompt

Different workflows use specialized modules because PDF reports, financial data
analysis, Google Workspace actions, and open-ended chat have different requirements
for state, safety, storage, and output format.

### Confirmation before external side effects

Gmail actions use confirmation before sending. This is important for AI systems that
can affect external accounts or communicate on behalf of a user.

## What I Would Improve Next

- Add end-to-end tests for the main chat and file-generation workflows
- Add Docker Compose for MongoDB, PostgreSQL/PGVector, and the FastAPI service
- Add screenshots or a short demo video for the web UI
- Expand CI coverage beyond the current unit tests
- Add API documentation examples for the main endpoints
- Split large modules into smaller service layers as the product grows

## User Documentation

For a user-facing feature guide, see:

```text
USER_MANUAL.md
```

## License

This project is private/internal unless a license is added.
