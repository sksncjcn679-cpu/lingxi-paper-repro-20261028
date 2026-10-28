This is a post-freeze, pre-analysis helper correction, not a collection-driver
change. Parent source `ba2dc0985cb9cec30c46625706f591c164fc6df92093c7747415250c692a4688`
is preserved byte for byte as `secondary_heterogeneity.py.before`.

Root static review found that the provider execution lock can be free between
formal controller stages. Previously `--allow-incomplete` could pass that instant
and run heavy analysis while the sequence was still active. The new guard always
requires an actual controller terminal receipt with `finished_utc`, an allowed
terminal status, and no child observed running. The receipt hash is archived in
the analysis output. `--allow-incomplete` changes coverage only.

No provider call, Docker task, numerical analysis, fixture, or lifecycle test has
been run for this change. Actual schema and synthetic boundary checks remain
pending until collection timing closes. All registered collection/statistical
sources and prospective analysis boundaries remain unchanged.
