# CLULatent v1.0.0

CLULatent makes media evidence playable by humans and readable by agents while
preserving timestamps, references, caveats, and uncertainty.

V1 includes the `clulatent.profile.v1` format contract, reference Python reader
and validator, CLI, offline conformance suite, deterministic portable
`.clulatent` archive, local playback evidence viewer, package/agent-read
indexes, and bounded ask handoffs.

```bash
pipx install clu-latent
clulatent demo --no-browser
clulatent build-video demo.mp4 -o demo.clulatent --profile v1
clulatent open demo.clulatent
```

Editable directory and portable ZIP transport forms use the same `.clulatent`
name. `.clubin` is not included.

CLULatent V1 compatibility certifies package/profile compatibility, not
semantic truth about media content.

Known limitations:

- evidence lanes are primarily structural/numeric, not general understanding;
- media builds require FFmpeg/ffprobe installed separately;
- the viewer is static/local, not a cloud collaboration product;
- ecosystem interoperability is new and needs external feedback.

Supported targets are Python 3.10–3.14 on Linux, macOS, and Windows as tested by
the public CI matrix. `SHA256SUMS.txt` authenticates the attached wheel, source
distribution, and synthetic demo bytes after download.

PyPI status is reported on the GitHub Release. GitHub publication does not
claim PyPI success when Trusted Publisher account configuration is pending.
