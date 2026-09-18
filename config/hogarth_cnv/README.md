# Existing Hogarth configuration

This folder preserves the earlier Hogarth dataset configuration and a schema-1
CNV mapping. That mapping is not accepted by the current schema-5 converter;
do not use this profile as a fresh-conversion template.

Current conversion uses `timeS` plus the CNV start-time origin, not the old `timeQ`
epoch-offset rule. Complete measured coordinate columns remain `(profile, z)`;
NMEA-only positions use `(profile)`. It does not reduce measured positions to
medians. Catalog definitions and shared defaults now live in `config_template/`.

For new conversion, run inspection on the intended processed source files and
let `convert-cnv /path/to/cnv` create a new dataset-specific profile. Do not assume
the historical Hogarth filename/header forms will all pass current validation.
Resolve inspection/conversion errors rather than reusing the old mapping.

For already-converted compatible NetCDF, the explicit paths in this profile can
still be used for QC. The existing Hogarth settings are not automatically replaced
by Walton Smith defaults. Its sensor/spike/rate/location limits include null
placeholders and climatology is empty; applicable tests therefore remain
NOT_EVALUATED until defensible settings are supplied. Station coordinates now
have a local copy under `location_test/`, not a shared runtime reference.

See [current conversion](../../docs/cnv-conversion-process.md),
[shared defaults](../../config_template/README.md), and the
[historical source audit](../../docs/cnv-data-findings.md).
