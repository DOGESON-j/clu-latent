# Python API

```python
from clu_latent import open_package

with open_package("demo.clulatent") as package:
    print(package.package_id)
    print(package.list_tracks())
    for event in package.iter_events("keyframes"):
        print(event.id, event.t_start_ms)
```

Public exports include `PackageReader`, `open_package`, `EventRecord`,
`TrackHandle`, `ReceiptHandle`, V1 profile contract/result types, and archive
pack/unpack/verify types and functions. `py.typed` declares inline typing.

Readers are read-only. Use the context manager so temporary archive material is
released deterministically.
