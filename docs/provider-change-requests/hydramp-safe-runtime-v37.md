# HydrAMP v37 safe-runtime provider change request

Status: blocking. Owner: HydrAMP runtime/provider release task. AMPgent compatibility patches are forbidden.

The frozen HydrAMP source revision `6590d2f4c2963f25d30669052a4c4a857e0e7279` executes
`joblib.load(decomposer_path)` in `amp/inference/inference.py:97`. A joblib artifact can
execute pickle payloads while loading, so the real runtime cannot truthfully satisfy the v37
contract `unsafe_deserialization_enabled: false`. The locked artifact hash establishes identity;
it does not make pickle deserialization safe.

The provider-owned replacement release is accepted only when all of the following hold:

1. It publishes new immutable source and model revisions without changing the frozen AMPgent
   selection, budget, or scoring protocol.
2. PCA/decomposer state is published as non-executable numeric data with per-file SHA-256 values.
3. Provider source loads that state without `pickle`, `joblib.load`, or another executable object
   deserializer. AMPgent must not monkeypatch or reconstruct provider internals.
4. Provider tests compare the old locked artifact and safe release on frozen seeds and prove exact
   sequence/order identity, or document only finite numerical differences within the project
   tolerance (`abs <= 1e-8` or `rel <= 1e-6`) that do not change sequence, label, rank, or direction.
5. A new runtime manifest locks executable, environment, source, model, request contract, adapter,
   and every file SHA-256, then passes the strict v37 verifier with
   `unsafe_deserialization_enabled: false`.
6. The release carries an immutable verification receipt suitable for PostgreSQL/object-store
   persistence and database-only replay.

Until these criteria pass, HydrAMP remains a required but blocked v37 generator. It must not be
silently dropped, substituted, or executed through the unsafe artifact.
