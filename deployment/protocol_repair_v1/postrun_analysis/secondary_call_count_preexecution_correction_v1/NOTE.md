Root checked original protocol Table3 and found that the new descriptive metric
list omitted actual provider attempt counts, although the original report had
included them. This is required original coverage, not an added endpoint or test
family. The minimal change adds the already recorded `provider_attempts` field
to the same task/chain summaries. No call is created and no usage is inferred.

Parent helper `5a6aa6df3f217b1a75082c82d22d7e989963006bc3d005a18370c0a0f874deb5`
and validation source `e751f1170ec20e13f5c56a4315c8ce19d048a8c6cb4ca9b4e8a06274ee95e5ad`
are preserved byte for byte in this directory. Current helper SHA256:
`0b3c25faba54d566c105e7faba227f0f1c25807852ea3c550068d023545844e8`.
The same nine-method validation is rebound to this source, with its existing
failed/missing-usage case also checking that observed three attempts survive
while an unobserved unit's attempt count remains unknown. No numerical test or
analysis has run. Primary d87, collection drivers and frozen boundaries are
unchanged.
