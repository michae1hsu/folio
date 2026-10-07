[Folio](../README.md) / [Documentation](README.md)

# Getting started

Install Folio on your machine, configure the required services, and open the workbench. Run all commands from the repository root.

## Prerequisites

| Dependency | Used for |
| --- | --- |
| Python 3.11 or newer | FastAPI backend and converters |
| Node.js 20.19+ or 22.12+ and npm | Building the React/Vite interface |
| Git | Cloning and local secret guards |
| LibreOffice (`soffice` or `libreoffice` on PATH) | Office document conversion |
| FFmpeg and ffprobe on PATH | Audio/video processing |
| Playwright Chromium | HTML rendering; installed by setup |
| Fonts for your source languages | Correct conversion and page previews |
| OpenAI API project with access to the configured models and Agents API | Parsing, transcription, embeddings, reports, and chat; Decisions API access if selected for grading |
| Google service-account JSON and a private GCS bucket | Cloud Markdown storage for **both** import choices |
| A TypeSafe JEV API key, if using JEV | Default document relevance grading and answer evaluation; OpenAI Decisions is an alternative |

Qdrant runs locally in embedded mode by default. A separate Qdrant server is optional. Network access is required for Google, OpenAI, and JEV operations. Library creation and ZIP validation themselves do not call a model.

## Install and start

Clone the repository and enter its root directory:

```bash
git clone https://github.com/michae1hsu/folio.git
cd folio
```

Use a separate checkout and virtual environment for each operating system.

### Windows (PowerShell)

Install Python, Node.js, LibreOffice, and FFmpeg. Ensure the executables listed above are on PATH, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Setup creates `.venv`, installs dependencies and Chromium, builds the interface, copies `.env.example` to `.env` only if absent, and enables the repository's Git hooks. Edit `.env` and configure the separate Google JSON file as described in [Configuration](configuration.md), then start:

```powershell
.\start.ps1
```

### Linux (Debian/Ubuntu)

Install Python with venv support, Git, and a supported Node.js version. Then run:

```bash
bash scripts/setup.sh
```

If LibreOffice or FFmpeg is missing, this script uses `apt-get` (and noninteractive `sudo` when needed) to install them with Noto CJK fonts. Playwright also installs browser system dependencies. If sudo is unavailable, install these dependencies with your administrator and rerun setup.

Follow [Configuration](configuration.md) to fill `.env` and set the separate Google JSON path, then start:

```bash
bash scripts/start.sh
```

### macOS

Install Python, Node.js, Git, FFmpeg, LibreOffice, and suitable fonts with your preferred installer. Make the LibreOffice executable available on PATH, for example:

```bash
export PATH="/Applications/LibreOffice.app/Contents/MacOS:$PATH"
bash scripts/setup.sh
# Configure .env and the separate Google JSON path before starting.
bash scripts/start.sh
```

Open **http://127.0.0.1:8765**. Stop the server with Ctrl+C. The launcher binds to localhost. Folio has no user authentication and is intended for a trusted, single-user machine; it is not a public hosting configuration.
