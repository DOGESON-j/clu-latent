# CLI reference

Core public commands:

```text
clulatent --version
clulatent --help
clulatent welcome
clulatent doctor
clulatent demo [--no-browser]
clulatent build-video INPUT -o OUTPUT.clulatent --profile v1
clulatent open PACKAGE
clulatent validate PACKAGE
clulatent profile inspect v1
clulatent profile verify PACKAGE
clulatent package-index verify PACKAGE
clulatent conformance run [--fixtures PATH]
clulatent pack PACKAGE_DIR -o PACKAGE.clulatent
clulatent unpack PACKAGE.clulatent -o PACKAGE_DIR
clulatent archive verify PACKAGE.clulatent
clulatent package inspect PACKAGE
clulatent agent-read summary PACKAGE
clulatent agent-read window PACKAGE --time 17s
clulatent ask PACKAGE "What evidence exists around 17s?"
```

Exit 0 means success/compatible/pass. Malformed input and failed verification
are nonzero; profile incompatibility uses exit 2 and unknown uses exit 3.
Existing historical commands retain their documented exit behavior.
