# Offline historical fixtures

These eight bounded files were copied byte-for-byte from the ignored local
2026-11-03 intake and SHE-20261103-060 publication helpers. The intake's pinned
SHA256 remains `cd1c3e5d62be3a59bb158b714162584c3ad67d9b8781fa264236ca501ce88c6b`.
The seven helper JSON files exercise the exact typed read-only-artifact verifier.

Tests copy these into disposable projects and mock the Host controller. This
directory is test data, is excluded from the package's `files` allowlist, and
does not authorize live creation, prompt, resume, publication, or risk approval.
No credentials, binary material, or complete production workspace is included.
The scoped attributes preserve exact bytes across Git checkouts.
