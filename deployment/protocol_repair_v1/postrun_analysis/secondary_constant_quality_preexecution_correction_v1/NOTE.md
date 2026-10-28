The unchanged parent `be54a3d2c495a4472026ea85b8fe05dfc23c9f4be1b675f9a6b980e8b887144d`
is preserved as `secondary_heterogeneity.py.before`.

Statistics-agent static review found that the constant-quality guard cleared the
absolute bootstrap CI while leaving its equally degenerate relative CI. The
minimal correction clears both and labels the relative CI as an invalid safety
interval. Defined/undefined draw counts and the already registered conservative
quality bounds remain intact. No main statistical or collection source changed.

Current helper SHA256:
`5a6aa6df3f217b1a75082c82d22d7e989963006bc3d005a18370c0a0f874deb5`.
This is pre-execution static preparation. No numerical validation has run.
