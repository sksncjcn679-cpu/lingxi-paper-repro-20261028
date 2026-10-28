Prepared metadata path correction only; quiet verification has NOT run.

Parent unchanged:
offline_manifest_preparation_v1/prepared_manifest.json
SHA256 a17192b7d5e0215bf8d45391f918474e79e764bc6c1ef2bf75d4d8ff4841e678

Child:
offline_manifest_preparation_v2/prepared_manifest.json
SHA256 280aa195e90e267648829586037c6b7eefa1b4db4b47c2e600f96a3482446b09
234031 bytes; status remains PREPARED_NOT_REHASHED.

Only one encoded metadata_references JSON key was replaced. Reversing that key
literal reproduces all original parent bytes, including CRLF and object order.
The historical consumer value remains SHA cbd12f31afe4360e2427136ee07a35e73579ffec5ef6b90c4395f971ee30fbf0,
53413 bytes. Its new key points to the existing exact preimage:
report_table_coverage_preexecution_correction_v2/saved_report_adapter_cbd12_preimage.py.
The current bb15 consumer was not substituted for the historical version.
All asset/raw/proof/source-root/value/status/timestamp fields are unchanged.

Original verifier unchanged:
prepare_offline_asset_manifest.py
SHA256 23acc866bdf6bf26b121b9037ea0907d16e662653b716258dcb4c5226ad890f6

Independent path_correction_receipt.json records parent/child hashes, old/new
paths, actual small preimage SHA/bytes and deep/byte equality checks.
Receipt SHA256 83d7c88f0280097ad12d9e7dd1a497681e98aecf61f8c4c039b7844ede350658.
This correction did not run verify, raw hashing, Docker, statistics or models,
and did not read new formal outcomes.

FUTURE ONLY: root must actually close real/retrieval timing and supply its
post-termination quiescence receipt. Replace only <ACTUAL_QUIET> below:

python -B deployment/protocol_repair_v1/postrun_analysis/prepare_offline_asset_manifest.py verify --prepared-manifest deployment/protocol_repair_v1/postrun_analysis/offline_manifest_preparation_v2/prepared_manifest.json --prepared-sha256 280aa195e90e267648829586037c6b7eefa1b4db4b47c2e600f96a3482446b09 --study-out paper_experiments_20261019_pilot/protocol_repair_20261020_v1 --quiescence-receipt paper_experiments_20261019_pilot/protocol_repair_20261020_v1/analysis/<ACTUAL_QUIET>.json --destination deployment/protocol_repair_v1/postrun_analysis/offline_manifest_verification_v2

The fresh verification destination is not yet created. Only actual success
creates its approved_offline_manifest.json with VERIFIED status. Failure is
retained and never auto-retried. Pilot's future one-command controller must use
this child path/SHA and that actual approved output; the correction receipt
cannot stand in for quiet verification.
