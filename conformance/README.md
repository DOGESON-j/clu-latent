# CLULatent V1 conformance

The public suite is deterministic, synthetic, and offline. It makes no model,
OCR, network, or external-media call. Run it with:

```bash
clulatent conformance run
```

The installable fixture manifest lives at
`src/clu_latent/data/conformance/fixture_manifest.json`. Directory fixtures are
generated into a temporary directory from fixed bytes and schema models.
Portable archive fixtures are generated and checked by the archive stage.

`COMPATIBLE` means package/profile compatibility only. It is not a
certification of semantic truth about media content.
