# Historical 02_CNV configuration

This folder preserves the earlier filename-identity rules and schema-2 mapping.
It is not the configuration used by the current schema-5 converter. The old
`prepare-cnv-identities`, `--input-dir`, and manifest-driven conversion workflow
are not supported by the current CLI.

For a cruise in the archive, use:

```bash
python main.py convert-cnv cnv_data/02_CNV/SAV1803_cnv
python main.py qc --profile config/sav1803/dataset_profile.json
python main.py erddap-xml --profile config/sav1803/dataset_profile.json
```

New dataset settings come from `config_template/`, not this directory. Existing
profiles remain independently editable. Do not assume every historical filename
or header is supported: inspection reports unresolved cases and conversion checks
the actual source. No full-archive success is implied by the SAV1803 verification.

See the [current workflow](../../docs/automatic-cnv-workflow.md) and
[historical design record](../../docs/02-cnv-conversion-process-proposal.md).
