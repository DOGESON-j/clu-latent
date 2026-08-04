<p align="center">
  <a href="https://github.com/DOGESON-j/clu-latent/releases"><img alt="GitHub release" src="https://img.shields.io/github/v/release/DOGESON-j/clu-latent?style=flat-square"></a>
  <a href="https://pypi.org/project/clu-latent/"><img alt="PyPI" src="https://img.shields.io/pypi/v/clu-latent?style=flat-square"></a>
  <a href="https://pypi.org/project/clu-latent/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/clu-latent?style=flat-square"></a>
  <a href="LICENSE"><img alt="BSD-3-Clause license" src="https://img.shields.io/github/license/DOGESON-j/clu-latent?style=flat-square"></a>
  <a href="docs/FORMAT_V1.md"><img alt="Profile V1" src="https://img.shields.io/badge/profile-clulatent.profile.v1-2563eb?style=flat-square"></a>
  <a href="CONTRIBUTING.md"><img alt="Contributions welcome" src="https://img.shields.io/badge/contributions-welcome-16a34a?style=flat-square"></a>
</p>

<p align="center">
  <img src="docs/assets/readme/clu-latent-hero.svg" alt="CLU Latent: local-first portable media evidence packages for humans and agents" width="100%">
</p>

<h3 align="center">Media that humans can play and software agents can inspect through bounded evidence.</h3>

<p align="center">
  <strong>MP4 plays media. CLULatent plays media plus evidence.</strong>
</p>

CLULatent turns video and media into a validated, timestamped, reviewable evidence package. It pairs source media with structured event tracks, references, caveats, hashes, receipts, indexes, and local inspection tools so humans and software agents can work from the same bounded evidence.

It is a **local-first package format and reference implementation**. No account, cloud service, analytics, telemetry, or model API is required for the core V1 workflow.

## What CLULatent offers

- ✨ **A clear media-evidence contract:** V1 packages use the stable `clulatent.profile.v1` compatibility profile instead of an undocumented collection of sidecar files.
- 🧭 **Timestamped evidence:** events are bounded to media time and connected through explicit references rather than free-floating descriptions.
- 👁️ **Human-reviewable playback:** `clulatent open` creates a self-contained local HTML viewer with package facts, media playback when available, timeline evidence, keyframes, event details, profile status, and caveats.
- 🤖 **Agent-readable retrieval:** token-budgeted summaries and time windows let an external agent inspect focused evidence without receiving an entire opaque media file.
- 🔐 **Validation and integrity:** manifests, package identity, paths, tracks, indexes, receipts, and archive members are checked before being trusted.
- 📦 **Portable transport:** packages can remain editable directories or become deterministic `.clulatent` ZIP containers with bounded verification and extraction.
- 🧪 **Offline conformance:** the bundled V1 fixture suite tests valid, malformed, unsafe, stale, future-safe, and archive cases without a model or network call.
- 🐍 **Typed Python access:** the read-only package API exposes manifests, tracks, events, receipts, locks, profile results, and archive operations.
- 🧩 **Optional evidence lanes:** packages may include keyframes, audio and speech events, visual-change candidates, changed-region candidates, evidence bundles, agent reviews, and agent-context artifacts.
- ⚖️ **Evidence, not truth:** compatibility proves that a package follows the contract. It does not certify what a scene means or guarantee that generated evidence is semantically correct.

## Quick demo

Install the CLI:

```bash
pipx install clu-latent
```

Check the environment and run the bundled synthetic demo without network access:

```bash
clulatent --version
clulatent doctor
clulatent demo --no-browser
```

Build a V1 evidence package from a video:

```bash
clulatent build-video input.mp4 -o evidence.clulatent --profile v1
clulatent profile verify evidence.clulatent
clulatent open evidence.clulatent
```

Inspect it through the bounded agent-read surface:

```bash
clulatent agent-read summary evidence.clulatent
clulatent agent-read window evidence.clulatent --time 17s
clulatent ask evidence.clulatent "What evidence exists around 17s?"
```

> `agent-read` and `ask` produce evidence maps and safe handoff prompts. They do not call a model or answer the question themselves.

Build operations require FFmpeg and ffprobe. Reading, validation, profile inspection, conformance, archive verification, and the bundled synthetic demo do not require network access.

## The core workflow

```text
source media
    │
    ▼
build-video / ingest
    │
    ├── manifest and source hash facts
    ├── timestamped JSONL evidence tracks
    ├── optional media and keyframes
    ├── receipts, validation, and lock information
    └── V1 package index and agent-read artifacts
            │
            ├── human: clulatent open
            ├── tools: open_package(...)
            └── agents: agent-read summary/window + ask handoff
```

A normal video file is optimized for playback. CLULatent adds a stable evidence layer around the media without pretending that structural evidence automatically equals understanding.

## Why not just MP4 plus loose JSON?

| Capability | Plain media file | Custom sidecar files | CLULatent V1 |
|---|---:|---:|---:|
| Play the original media | Yes | Depends | Yes, when media is included |
| Stable evidence profile | No | Project-specific | `clulatent.profile.v1` |
| Timestamped event envelopes | No | Custom | Declared JSONL tracks |
| Bounded agent retrieval | No | Custom | Summary, time window, and ask handoff |
| Package validation | Media-level only | Custom | Reader, validator, profile, and index checks |
| Reproducible transport | Video only | Multiple files | Editable directory or deterministic ZIP |
| Safe extraction limits | Not applicable | Custom | Built-in traversal and resource defenses |
| Offline compatibility fixtures | No | Custom | Bundled V1 conformance suite |
| Human evidence viewer | Video player | Custom | Self-contained local viewer |

CLULatent is most useful when media must move between people, programs, or agents **without losing the evidence boundaries, references, uncertainty, and audit context around it**.

## Installation

Recommended:

```bash
pipx install clu-latent
```

Other supported installation paths:

```bash
uv tool install clu-latent
python -m pip install clu-latent
uvx clu-latent --help
```

Install directly from the V1 release tag:

```bash
python -m pip install "clu-latent @ git+https://github.com/DOGESON-j/clu-latent@v1.0.0"
```

For local development:

```bash
git clone https://github.com/DOGESON-j/clu-latent.git
cd clu-latent
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[visual,dev]"
python -m pytest
```

Supported targets are Python 3.10–3.14 on Linux, macOS, and Windows. FFmpeg and ffprobe are external system tools used for media-building operations.

## CLI overview

### Build, inspect, and validate

```bash
clulatent doctor
clulatent demo --no-browser
clulatent build-video INPUT -o OUTPUT.clulatent --profile v1
clulatent open PACKAGE
clulatent validate PACKAGE
clulatent package inspect PACKAGE
```

### Profile, package index, and conformance

```bash
clulatent profile inspect v1
clulatent profile verify PACKAGE
clulatent package-index verify PACKAGE
clulatent conformance run
```

### Agent-oriented retrieval

```bash
clulatent agent-read summary PACKAGE
clulatent agent-read window PACKAGE --time 17s
clulatent ask PACKAGE "What evidence exists around 17s?"
```

### Portable archive transport

```bash
clulatent pack PACKAGE.clulatent/ -o PACKAGE.clulatent
clulatent archive verify PACKAGE.clulatent
clulatent unpack PACKAGE.clulatent -o PACKAGE-copy.clulatent/
```

Exit code `0` means success, compatibility, or pass. Profile incompatibility uses exit code `2`; unknown compatibility uses exit code `3`.

See the complete [CLI reference](docs/CLI_REFERENCE.md).

## Human playback viewer

`clulatent open PACKAGE` generates a self-contained HTML viewer outside the input package. The viewer can expose:

- package identity and source facts;
- playable media when it is included;
- an evidence timeline and track filters;
- bounded event details and references;
- recorded keyframes;
- V1 profile and package-index status;
- visible caveats and missing-evidence conditions.

The viewer uses no CDN, remote font, analytics, or external script. It is a local evidence playback surface, not a cloud collaboration product and not a semantic-certification interface.

## Agent-read perception pyramid

Agents do not always need every file, frame, or event. CLULatent provides progressively focused access:

1. **Summary:** package identity, track inventory, coverage, caveats, and ranked evidence.
2. **Window:** bounded evidence around a requested media time.
3. **Ask handoff:** a safe prompt bundle that tells another agent what evidence exists, what is missing, and what it must not conclude.
4. **Package reader:** structured iteration over exact tracks and event records when deeper inspection is necessary.

This keeps retrieval bounded and explicit. CLULatent supplies the evidence context; an external model, rule system, application, or human reviewer decides what to do with it.

## Optional VAD and transcription

The baseline package and core CLI do not require machine learning. Video ingest can explicitly enable optional speech-processing extras:

```bash
python -m pip install "clu-latent[vad]"
clulatent ingest input.mp4 -o evidence.clulatent --vad
```

```bash
python -m pip install "clu-latent[whisper]"
clulatent ingest input.mp4 -o evidence.clulatent \
  --transcribe \
  --whisper-model base
```

A local faster-whisper model path avoids model resolution through the network:

```bash
clulatent ingest input.mp4 -o evidence.clulatent \
  --transcribe \
  --whisper-model-path /path/to/local/model
```

These operations never run unless explicitly requested. Speech output remains candidate evidence and does not become guaranteed transcription truth.

## Non-semantic visual evidence

CLU Latent can record and retrieve structural visual evidence without claiming to understand a scene. Optional capabilities include:

- stored keyframe evidence;
- bounded pixel-difference candidates between adjacent keyframes;
- the strongest changed grid region for a visual-change candidate;
- evidence bundles for a bounded time range;
- deterministic rule-based agent review of an evidence bundle.

These lanes do **not** implicitly perform object recognition, face recognition, identity inference, OCR, emotion detection, intent recognition, or scene certification.

## Package anatomy

<p align="center">
  <img src="docs/assets/readme/package-anatomy.svg" alt="CLU Latent package anatomy: manifest, media, events, index, and integrity" width="76%">
</p>

A V1 package records a manifest and source-hash facts, declared timestamped JSONL tracks, receipts, derived indexes, optional keyframes and media, validation status, and lock information.

The two supported package forms are:

- **Editable:** a directory named `PACKAGE.clulatent/`.
- **Portable:** a deterministic ZIP container named `PACKAGE.clulatent` with media type `application/vnd.clulatent+zip`.

The future `.clubin` compiled form is not implemented and is not part of V1.

Optional capabilities may be absent without invalidating the baseline profile. Unknown future-safe tracks remain visible and safely readable without being silently promoted to a known canonical evidence lane.

## Deterministic portable archives

Packing uses stable member ordering, normalized timestamps and regular-file permissions, and deterministic compression choices. It records no host path, user name, or machine metadata.

Default verification limits include:

| Limit | Default |
|---|---:|
| Archive members | 10,000 |
| One uncompressed file | 512 MiB |
| Total uncompressed bytes | 2 GiB |
| Compression ratio | 200:1 |
| Nested archives | 0 |

Archive verification and extraction reject traversal, absolute paths, Windows drive escapes, links and special files, encrypted members, unsupported compression, case-folded duplicates, file/directory prefix conflicts, suspicious ratios, and configured limit violations. Extraction does not use `ZipFile.extractall()`.

## Python API

```python
from clu_latent import open_package

with open_package("demo.clulatent") as package:
    print(package.package_id)
    print(package.list_tracks())

    for event in package.iter_events("keyframes"):
        print(event.id, event.t_start_ms)
```

Public exports include `PackageReader`, `open_package`, `EventRecord`, `TrackHandle`, `ReceiptHandle`, V1 profile contract/result types, and archive pack/unpack/verify functions and result types. The package includes `py.typed` for inline typing.

Readers are read-only. Use the context manager so temporary material from portable archives is released deterministically.

See the [Python API guide](docs/PYTHON_API.md).

## V1 profile and conformance

The public compatibility identifier is:

```text
clulatent.profile.v1
```

The format contract version is `1.0`.

```bash
clulatent profile inspect v1
clulatent profile verify PACKAGE
clulatent conformance run
```

The deterministic offline conformance suite covers minimal, full, optional, future-safe, and portable valid packages, plus malformed manifests and tracks, path traversal, identity failures, stale or incorrect indexes, invalid references, unsafe archive members, and archive resource limits.

Requirement states are `PASS`, `FAIL`, `MISSING`, `UNKNOWN`, and `NOT_APPLICABLE`. Overall compatibility is `COMPATIBLE`, `INCOMPATIBLE`, or `UNKNOWN`. Unknown evidence is never silently converted into a pass.

## Security model

Every package and archive is treated as untrusted input.

- Package-relative paths pass containment and symlink checks.
- JSON, JSONL, manifests, receipts, indexes, events, and archive operations have bounded resource use.
- External processes receive argument arrays and are never invoked with `shell=True`.
- Read commands must not mutate the input package or its receipts.
- `clulatent open` writes generated viewer files only to an external output directory.
- Locks provide tamper evidence, not cryptographic authenticity.
- Hashes establish byte identity, not semantic accuracy.
- Core reader and conformance operations store no credentials, send no telemetry, and perform no model call.

Report vulnerabilities privately according to [SECURITY.md](SECURITY.md). Never attach customer media, credentials, tokens, personal data, or rights-unverified voice material to a public issue.

## Where CLULatent fits

Within the broader CLU vision, CLULatent is the **public media-evidence layer**, not the entire agent system. It gives an external human, program, or agent a bounded and inspectable package instead of handing it an opaque media blob.

This repository does not run a private CLU environment, expose a developer machine, provide a network route into another system, or contain private THE-GRID state. A separate system may consume CLULatent through the public CLI, package format, or Python API without granting this project access back into that system.

## What CLULatent is not

CLULatent V1 is not:

- a video codec;
- `.clubin`;
- general video understanding;
- implicit object, face, identity, emotion, intent, or scene recognition;
- guaranteed transcription truth;
- semantic certification of media content;
- a replacement for human review;
- a hosted cloud collaboration service;
- an agent runtime or LLM provider.

It supplies structured candidate evidence, bounded retrieval, and compatibility checks. Judgment still belongs to the consuming human or system.

## Project status

**Public release:** `v1.0.0`  
**Stable format profile:** `clulatent.profile.v1`  
**Python:** 3.10–3.14  
**Platforms:** Linux, macOS, Windows  
**License:** BSD-3-Clause

V1 includes the profile contract, reference reader and validator, CLI, offline conformance suite, deterministic portable archive, local playback viewer, package index, agent-read surfaces, and bounded ask handoffs.

Current limitations:

- evidence lanes are primarily structural and numeric rather than general semantic understanding;
- FFmpeg and ffprobe must be installed separately for media builds;
- the viewer is static and local;
- ecosystem interoperability is new and needs external feedback;
- some advanced implementation APIs remain experimental even though the V1 format profile is stable.

### Current development direction

The active V1.1 design work is tracked in [issue #7](https://github.com/DOGESON-j/clu-latent/issues/7). Planned work includes viewer path hardening, easier opt-in transcription from the one-command builder, and provenance-rich semantic enrichment that remains explicitly separated from trusted structural evidence.

Planned functionality is not presented as part of the released V1 contract until it is implemented, tested, and released.

## Documentation

<p align="center">
  <img src="docs/assets/readme/documentation-contribute.svg" alt="CLU Latent documentation and contribution overview" width="100%">
</p>

- [Quickstart](docs/QUICKSTART.md)
- [V1 format](docs/FORMAT_V1.md)
- [Archive format and limits](docs/ARCHIVE_V1.md)
- [Security model](docs/SECURITY_MODEL.md)
- [Conformance](docs/CONFORMANCE.md)
- [Compatibility](docs/COMPATIBILITY.md)
- [CLI reference](docs/CLI_REFERENCE.md)
- [Python API](docs/PYTHON_API.md)
- [Changelog](CHANGELOG.md)

## Contributing

Issues, design feedback, interoperability reports, tests, documentation improvements, and bounded pull requests are welcome.

Use synthetic or clearly redistributable fixtures. Never commit private media, credentials, personal data, private-system paths, or rights-unverified voice material.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[visual,dev]"
python -m pytest
```

Changes affecting the V1 profile, manifest rules, archive limits, safe extraction, read-only behavior, or public APIs require focused tests and documentation. Generated evidence must remain bounded, referenced, caveated, and clearly distinguished from semantic truth.

Read [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [SECURITY.md](SECURITY.md), and [SUPPORT.md](SUPPORT.md) before submitting a change.

## License

CLULatent is distributed under the [BSD 3-Clause License](LICENSE).
