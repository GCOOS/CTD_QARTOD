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

These settings are not global defaults for every CTD dataset. New datasets
should copy `config_template/` into their own folder under `config/`
and validate their own mappings and QC limits.
