# Quickstart

Install with `pipx install clu-latent`, then:

```bash
clulatent --version
clulatent doctor
clulatent demo --no-browser
clulatent build-video demo.mp4 -o demo.clulatent --profile v1
clulatent profile verify demo.clulatent
clulatent open demo.clulatent
```

Build operations need FFmpeg/ffprobe. Read, validation, profile, conformance,
archive, and synthetic demo commands do not need network access.

Inspect bounded agent evidence with:

```bash
clulatent agent-read summary demo.clulatent
clulatent agent-read window demo.clulatent --time 17s
clulatent ask demo.clulatent "What evidence exists around 17s?"
```
