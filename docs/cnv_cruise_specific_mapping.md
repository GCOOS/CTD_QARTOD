# name N:

| Index | CNV field | Header meaning and units |
| --- | --- | --- |
| 0 | `scan` | Scan count |
| 1 | `depSM` | Salt-water depth, m |
| 2 | `c0S/m` | Conductivity, S/m |
| 3 | `t090C` | ITS-90 temperature, degrees C |
| 4 | `sal00` | Practical salinity, PSU |
| 5 | `oxsatMg/L` | Oxygen saturation, Weiss, mg/L |
| 6 | `sbeox0Mg/L` | SBE 43 oxygen, mg/L |
| 7 | `flECO-AFL` | WET Labs ECO-AFL/FL fluorescence, mg/m3 |
| 8 | `wetCDOM` | WET Labs CDOM fluorescence, mg/m3 |
| 9 | `turbWETntu0` | WET Labs ECO turbidity, NTU |
| 10 | `density00` | Density, kg/m3 |
| 11 | `svCM` | Chen-Millero sound velocity, m/s |
| 12 | `latitude` | Per-scan NMEA latitude, degrees |
| 13 | `longitude` | Per-scan NMEA longitude, degrees |
| 14 | `oxsolMg/L` | Garcia and Gordon oxygen solubility, mg/L |
| 15 | `timeS` | Elapsed seconds |
| 16 | `timeM` | Elapsed minutes |
| 17 | `timeH` | Elapsed hours |
| 18 | `timeJ` | Julian day-of-year value |
| 19 | `timeQ` | NMEA seconds since 2000-01-01 |
| 20 | `altM` | Altimeter range, m |
| 21 | `flag` | Sea-Bird processing flag |

Standardized mapping

| CNV field | netCDF variable | Output units | Treatment |
| --- | --- | --- | --- |
| `depSM` | `depth` | `m` | Preserved; positive down |
| `c0S/m` | `sea_water_electrical_conductivity` | `S/m` | Preserved unchanged |
| `t090C` | `sea_water_temperature` | `deg C` | Preserved unchanged |
| `sal00` | `sea_water_salinity` | `PSU` | Preserved unchanged |
| `sbeox0Mg/L` | `dissolved_oxygen` | `mg/l` | Preserved unchanged |
| `oxsolMg/L` | `oxygen_saturation` | `mg/l` | Preserved unchanged |
| `oxsatMg/L` | `oxygen_saturation_2` | `mg/l` | Preserved unchanged |
| `flECO-AFL` | `chlorophyll_concentration` | `mg/m^3` | Preserved unchanged |
| `wetCDOM` | `CDOM` | `mg/m^3` | Preserved unchanged |
| `turbWETntu0` | `sea_water_turbidity` | `NTU` | Preserved |
| `density00` | `sea_water_density` | `kg/m^3` | Preserved as density |
| `svCM` | `sound_velocity` | `m/s` | Preserved |
| `altM` | `altimeter` | `m` | Preserved |
| `scan` | `scan` | `1` | Preserved |
| `flag` | `flag` | `1` | Preserved, non-QARTOD |
| `timeS` | `time_elapsed` | `seconds` | Preserved |
| `timeM` | `seabird_elapsed_minutes` | `minutes` | Preserved |
| `timeH` | `seabird_elapsed_hours` | `hours` | Preserved |
| `timeJ` | `seabird_julian_day` | `days` | Preserved |
| `timeQ` | `time` | seconds since 1970-01-01 | Configured epoch offset applied to every sample |
| `latitude` | `latitude_sample` | `degrees_north` | Every scan preserved |
| `longitude` | `longitude_sample` | `degrees_east` | Every scan preserved |

Instrument sensor:

| Component | Source-reported model/type | Channel | SensorID | Serial number | Calibration date | Calibration fields |
| --- | --- | --- | --- | --- | --- | --- |
| CTD system | Sea-Bird SBE 25plus | — | — | not supplied | — | — |
| Temperature | `TemperatureSensor` | 1 | 55 | 6623 | 24-Jul-25 | G, H, I, J, F0… |
| Conductivity | `ConductivitySensor` | 2 | 3 | 0351 | 23-Jul-25 | G, H, I, J, CPcor… |
| Pressure | `PressureSensor` | 3 | 46 | 1230 | 11-Sep-25 | PA, PTEMP, PTCA, PTCB… |
| Oxygen | SBE 43 / `OxygenSensor` | 4 | 38 | 1959 | 18-Jul-25 | Soc, A–E, Tau20… |
| CDOM | WET Labs ECO CDOM | 6 | 19 | FLCDRTD-1111 | 25-Jul-2025 | Scale factor, Vblank |
| Chlorophyll | WET Labs ECO-AFL/FL | 8 | 20 | FLNTURTD-2022 | 24-Jul-2025 | Scale factor, Vblank |
| Turbidity | WET Labs ECO-NTU | 9 | 67 | FLNTURTD-2022 | 24-Jul-2025 | Scale factor, dark voltage |
| Altimeter | `AltimeterSensor`; exact model unavailable | 10 | 0 | not supplied | 2020 | Scale factor, offset |

metadata source matrix

| Field | CNV | Derived | Configured | Unavailable |
| --- | --- | --- | --- | --- |
| Cruise, source station, sequence | filename | canonical identifiers/output stem |  |  |
| Measurements, source descriptions/units | yes | configured NetCDF names; values and units preserved | `config/hogarth_cnv/cnv_mapping.json` |  |
| Cast time and spatial coverage | per-scan values | profile coordinates and min/max |  |  |
| CTD and sensor identity | SBE 25plus header; XML channels, types, SensorID, serials, calibration dates | scalar instrument variables, excluding SensorID |  | exact model for some sensors; CTD and altimeter serials |
| Sensor calibration coefficients | embedded XML | flattened calibration JSON |  | calibration certificates and traceability records |
| Direct variable-to-sensor relationship | field names and descriptions, but no formal key |  | dataset sensor map for QC lookup only | authoritative relationship for derived fields |
| DatCnv and oxygen-correction provenance | yes | normalized provenance text |  |  |
| Platform long/short name |  |  |  | yes; official FIO source available at the publication boundary |
| Program, project, institution, naming authority |  |  |  | yes |
| Publisher, license, sea name, country |  |  |  | yes |
| Creator and contributor identity/contact |  |  |  | yes |
| References, acknowledgment, info URL |  |  |  | yes |
| ERDDAP metadata URL |  |  |  | unavailable until publication |
| Creation/issue/modified dates and product version |  |  |  | yes; conversion time is not substituted |
| Fixed program-level geospatial bounds |  | per-cast bounds only |  | yes |
| Undocumented Sea-Bird post-processing stages |  |  |  | yes |
| QARTOD limits and thresholds |  |  | empty Hogarth templates | yes; scientific approval required |