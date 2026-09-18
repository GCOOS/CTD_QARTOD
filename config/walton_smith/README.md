# Walton Smith CTD dataset configuration

This folder owns the QC configuration for the legacy Walton Smith CTD NetCDF
dataset family.

- `dataset_profile.json` is the default profile used by `qc`, `viz`, and
  profile-driven ERDDAP generation.
- `dataset_profile_output2.json` is the alternate-output profile.
- Each test subfolder contains the station information, mappings, limits, or
  thresholds used for this dataset family.
- `qc_test_modes` and `erddap` in each profile are authoritative; the CLI does
  not override dataset paths or XML-generation policy.

These files configure this dataset, not a shared runtime limit service. Their QC
values were copied into `config_template/` as the initial reusable defaults. New
datasets initialize independent settings from that template; later edits here do
not change those defaults or other datasets. Review copied thresholds and station
references for each new dataset. See [the template guide](../../config_template/README.md).
