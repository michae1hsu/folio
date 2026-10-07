# Third-party software and licenses

This reference describes Folio's direct Python and JavaScript dependencies, bundled tokenizer data, external conversion tools, and hosted services. Package versions below correspond to [requirements.txt](requirements.txt) and [package-lock.json](package-lock.json). Additional dependencies and native libraries retain their own licenses; the notices shipped with the exact installed packages and binaries remain authoritative.

These summaries explain third-party terms. They do not replace the license texts, relicense a component, or grant additional rights to Folio itself.

## PDF processing: PyMuPDF and MuPDF

Folio uses **PyMuPDF 1.28.2**, backed by **MuPDF**, to create and inspect PDFs, append supplementary pages, render page previews, and extract text for evaluation evidence. PyMuPDF is distributed under **GNU AGPL v3 or a separate Artifex commercial license**. See [Artifex's licensing explanation](https://pymupdf.io/licensing) and the [AGPL v3 license text](https://choosealicense.com/licenses/agpl-3.0/).

**AGPL permits commercial use.** Its conditions include preserving notices and providing corresponding source code under the applicable license when distributing covered software. Running a modified covered program as a network service also requires offering the corresponding source to users who interact with it remotely. The obligations concern the covered work and can extend to software combined with an AGPL library; an application's own permissive notice does not remove them. See AGPL sections 4–6 and 13.

Artifex offers separate commercial terms for uses that cannot meet the AGPL conditions, including relevant proprietary integrations. Those permissions come from an agreement with Artifex; this repository does not supply one. The applicable route depends on the way the software is used, modified, combined, and distributed.

## Python dependencies

Package links lead to the upstream-published release metadata. License texts are included in the installed distribution where supplied, usually in its `.dist-info/licenses/` directory or a `LICENSE` / `COPYING` file.

| Package | Version | Use in Folio | Declared license |
| --- | --- | --- | --- |
| [FastAPI](https://pypi.org/project/fastapi/0.142.2/) | 0.142.2 | Backend HTTP API | MIT |
| [Uvicorn](https://pypi.org/project/uvicorn/0.54.0/) | 0.54.0 | Local ASGI server | BSD-3-Clause |
| [OpenAI Python SDK](https://pypi.org/project/openai/3.24.0/) | 3.24.0 | Parsing, transcription, embeddings, reports, and Agent API client | Apache-2.0 |
| [python-dotenv](https://pypi.org/project/python-dotenv/1.2.4/) | 1.2.4 | Backend environment configuration | BSD-3-Clause |
| [PyMuPDF](https://pypi.org/project/PyMuPDF/1.28.2/) | 1.28.2 | PDF generation, inspection, rendering, and text extraction | AGPL v3 or Artifex commercial terms |
| [Pillow](https://pypi.org/project/Pillow/12.3.0/) | 12.3.0 | Image decoding, orientation, compositing, and frames | MIT-CMU; bundled native libraries have additional notices |
| [Playwright](https://pypi.org/project/playwright/1.63.0/) | 1.63.0 | Browser automation for HTML-to-PDF rendering | Apache-2.0; browser binaries have separate notices |
| [openpyxl](https://pypi.org/project/openpyxl/3.1.5/) | 3.1.5 | Spreadsheet cells, formulas, and hidden-sheet supplements | MIT |
| [HTTPX](https://pypi.org/project/httpx/0.28.1/) | 0.28.1 | HTTP requests, including JEV evaluation | BSD-3-Clause |
| [google-auth](https://pypi.org/project/google-auth/2.59.1/) | 2.59.1 | Google service-account authentication | Apache-2.0 |
| [google-cloud-storage](https://pypi.org/project/google-cloud-storage/3.16.0/) | 3.16.0 | GCS object storage and signed links | Apache-2.0 |
| [qdrant-client](https://pypi.org/project/qdrant-client/1.19.1/) | 1.19.1 | Embedded or remote vector retrieval | Apache-2.0 |
| [tiktoken](https://pypi.org/project/tiktoken/0.14.0/) | 0.14.0 | Token counting and retrieval chunk boundaries | MIT |
| [Jieba](https://pypi.org/project/jieba/0.42.1/) | 0.42.1 | Word segmentation for sparse retrieval | MIT |
| [setuptools](https://pypi.org/project/setuptools/83.0.0/) | 83.0.0 | Python packaging support | MIT; vendored dependencies retain their notices |

The `requests` extra on `google-auth` installs additional dependencies. The table names direct requirements; it is not a complete inventory of every package or native library installed transitively.

## Frontend and build dependencies

The versions shown are resolved versions in the lockfile. Runtime libraries become part of the built interface; Vite is the development and build tool.

| Package | Version | Use in Folio | Declared license |
| --- | --- | --- | --- |
| [React](https://github.com/facebook/react) | 19.3.0 | Interface components | MIT |
| [React DOM](https://github.com/facebook/react) | 19.3.0 | Browser rendering | MIT |
| [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.16 | Sanitizing rendered Markdown and diagrams | Apache-2.0 OR MPL-2.0 |
| [Lucide React](https://github.com/lucide-icons/lucide) | 0.468.0 | Interface icons | ISC; includes Feather attribution |
| [Marked](https://github.com/markedjs/marked) | 15.0.12 | Markdown rendering | MIT |
| [Mermaid](https://github.com/mermaid-js/mermaid) | 11.17.2 | Diagrams inside Markdown | MIT |
| [Vite](https://github.com/vitejs/vite) | 7.3.6 | Frontend development server and production build | MIT |

DOMPurify offers a choice between its Apache-2.0 and MPL-2.0 terms. Lucide's distributed license retains attribution for portions originating in Feather under MIT; preserve that notice along with the ISC text.

## Common license conditions

These license families allow commercial activity subject to their conditions. The brief descriptions below concern the named components, not a blanket permission for the complete application.

| License family | Main conditions to retain when sharing or modifying the component |
| --- | --- |
| MIT, ISC, MIT-CMU | Preserve the required copyright, permission, and warranty notices. Respect any restrictions on using authors' names for endorsement or publicity. |
| BSD-3-Clause | Preserve the copyright, conditions, and disclaimer in source or binary distributions; do not imply endorsement by the authors. |
| Apache-2.0 | Include the license, preserve relevant notices and any required `NOTICE` attribution, and identify modified files. Its patent and trademark provisions also apply. |
| MPL-2.0 | When distributing covered software, make the covered source files, including modifications, available under MPL and preserve required notices. |
| AGPL v3 | Preserve notices and satisfy corresponding-source and copyleft conditions for covered distributions and modified network services, as described for PyMuPDF above. |

For the full wording, see the [MIT](https://opensource.org/license/mit), [ISC](https://opensource.org/license/isc-license-txt), [MIT-CMU](https://spdx.org/licenses/MIT-CMU.html), [BSD-3-Clause](https://opensource.org/license/bsd-3-clause), [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0), [MPL-2.0](https://www.mozilla.org/en-US/MPL/2.0/), and [AGPL v3](https://choosealicense.com/licenses/agpl-3.0/) texts.

## External tools and fonts

Folio invokes these tools as separate installed programs. Their binaries are not stored in this repository. An installer, container, or other distribution that includes them must also satisfy the licenses of that actual build and its bundled dependencies.

| Component | Use in Folio | License information |
| --- | --- | --- |
| [LibreOffice](https://www.libreoffice.org/licenses/) | Office documents and slide decks to PDF | MPL-2.0, with other components under their own terms. The installed distribution contains the applicable notices. |
| [FFmpeg and ffprobe](https://www.ffmpeg.org/legal.html) | Complete audio-track preparation and media inspection | LGPL-2.1-or-later by default; enabling GPL components changes the build's license. Other linked components and build options can add conditions or restrict redistribution. |
| [Chromium](https://chromium.googlesource.com/chromium/src/+/main/LICENSE) | Browser engine used by Playwright for HTML rendering | BSD-style Chromium license plus the licenses of bundled third-party components. Playwright's Apache license does not replace them. |
| [Noto CJK fonts](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE) | CJK text rendering in the Linux setup | SIL Open Font License 1.1. Bundling or modifying the fonts has conditions, including any reserved font names; see the [OFL](https://openfontlicense.org/). Other installed fonts retain their own terms. |
| [Gitleaks](https://github.com/gitleaks/gitleaks/blob/v8.30.1/LICENSE) | Local hooks and CI secret scanning; downloaded separately, never bundled into Folio | MIT; preserve upstream notices when redistributing the executable. |

`ffmpeg -L` and `ffmpeg -buildconf` show the installed build's license and configuration. Retain the relevant licenses, notices, and corresponding-source materials when required by the components you redistribute. For an optional remote Qdrant deployment, the server or hosted service has its own applicable terms in addition to the Python client's license.

## Bundled tokenizer material

`vendor/tiktoken/cl100k_base.tiktoken` is OpenAI's `cl100k_base` vocabulary. The source URL, SHA-256, and usage are documented in [vendor/tiktoken/README.md](vendor/tiktoken/README.md). Its MIT copyright and license text are retained in [vendor/tiktoken/LICENSE](vendor/tiktoken/LICENSE). Preserve those notices when redistributing this bundled material.

## Hosted services

An open-source client library does not grant access to its provider's hosted service or model. Folio uses credentials supplied by the operator; API access, billing, acceptable use, and data processing remain subject to the applicable service agreement.

| Service | Used for | Applicable terms |
| --- | --- | --- |
| OpenAI | Document parsing, transcription, reports, embeddings, Agent chat, prompt suggestions, and optional Decisions grading | [OpenAI Services Agreement](https://openai.com/policies/services-agreement/) and any applicable account agreement. |
| Google Drive and Cloud Storage | Reading shared source folders and storing generated Markdown | [Google APIs Terms](https://developers.google.com/terms), [Google Cloud Terms](https://cloud.google.com/terms), and applicable service-specific or account agreements. |
| TypeSafe JEV | Full-document relevance grading and answer evaluation | The applicable TypeSafe product/API agreement. Its public [website Terms of Use](https://typesafe.ai/legal/terms) distinguish site use from separately agreed product and service terms. |

See [Data, privacy, and costs](docs/privacy-and-costs.md) for the content sent to each provider and the resources retained locally or remotely.
