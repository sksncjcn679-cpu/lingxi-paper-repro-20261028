Parent `fad9eb0b6610a4f38bc53d1db16e64e21a2441bd448d0e4e4d16cf71f3239015`
is preserved byte for byte as `secondary_heterogeneity.py.before`.

Before any numerical execution, root and the statistics agent independently
confirmed that `nanquantile` discarded bootstrap draws whose relative-effect
denominator was zero. A finite interval conditional on the remaining draws was
not the complete resampling interval. The helper now retains all10000 draws,
counts defined/undefined ratios, and reports a relative CI only if every draw is
defined and finite. Absolute-difference intervals are unchanged. No draws are
dropped or resampled, and no primary statistical entry or collection source is
changed. This is an operational missing-value correction, not a new test or
research condition.

Current helper SHA256:
`be54a3d2c495a4472026ea85b8fe05dfc23c9f4be1b675f9a6b980e8b887144d`.
No Docker/model/analysis/synthetic numerical test has been executed for this
correction. Its small pure-data boundary validation remains pending until the
real timing window closes.
