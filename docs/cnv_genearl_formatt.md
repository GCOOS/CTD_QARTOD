# CNV files

> Historical source/design record, not the current conversion contract. Source observations below are retained, but old mappings, timeQ/coordinate reduction rules, metadata ownership and CLI examples are superseded. Use the [current conversion process](cnv-conversion-process.md), [automatic workflow](automatic-cnv-workflow.md), and [shared template guide](../config_template/README.md). The current converter requires schema 5 and uses timeS, source-specific coordinate dimensions, and catalog-owned attributes.

filename:  <cruise>_*Stn.<station>_*<sequence>_DatCnv_processed.cnv

for example: HG26193_Stn.BG16_030_DatCnv_processed.cnv

- Cruise: `HG26193`
- Source station: `BG16`
- Processing/cast sequence: `030`

Each file contains 4 sections:

1. General metadata

Lines beginning with `*` describe the cast and acquisition:

```
* Sea-Bird SBE 25plus Data File:
* Temperature SN = 6623
* NMEA Latitude = 27 48.08 N
* NMEA Longitude = 082 52.90 W
* Real-Time Sample Interval = 0.1250 seconds
```

These can include instrument serial numbers, software version, acquisition time, position, and source filename.

1. Column definitions and processing metadata

Lines beginning with `#` define the table:

```
# nquan = 22
# nvalues = 3
# name 0 = scan: Scan Count
# name 1 = depSM: Depth [salt water, m]
# name 2 = c0S/m: Conductivity [S/m]
# name 3 = t090C: Temperature [ITS-90, deg C]
# bad_flag = -9.990e-29
# span N = 1, 2
```

Important entries are:

- `nquan`: number of columns in every data row.
- `nvalues`: number of data rows.
- `# name N`: meaning and units of column position `N`.
- `start_time`: time corresponding to the first scan.
- `interval`: nominal sampling interval.
- `bad_flag`: numeric sentinel representing missing data.
- `span N`: minimum, maximum value of the sequence

The column schema is positional. The first number in every row corresponds to `name 0`, the second to `name 1`, and so forth.

1. Sensor and calibration records

The header contains an XML-like sensor block, with each line still prefixed by `#`:

```
# <Sensors count="11" >
#   <sensor Channel="1" >
#     <TemperatureSensor SensorID="55" >
#       <SerialNumber>6623</SerialNumber>
#       <CalibrationDate>24-Jul-25</CalibrationDate>
#       <G>4.31743361e-003</G>
#     </TemperatureSensor>
```

It records sensor types, channels, serial numbers, calibration dates, and coefficients. The numeric table has already been processed using Sea-Bird software; these coefficients are provenance, not instructions to reprocess every value.

we can get:

- Sensor channel
- Sensor type
- Serial number
- Calibration date
- All calibration coefficients

1. Numeric sample table

The header ends with exactly:

```
*END*
```

Everything after that is the measurement table:

```
1  2.111  6.321208  32.1666  36.6515 ... 3.99  0.000e+00
2  2.175  6.311456  32.1654  36.5883 ... 3.97  0.000e+00
```

The table is:

- whitespace-delimited, not comma-separated;
- one scan per row;
- entirely numeric;
- allowed to use ordinary or scientific notation;
- required to have exactly `nquan` values per row.

[CNV cruise-specific mapping:](https://app.notion.com/p/CNV-cruise-specific-mapping-3c09b541ddc880728ea4dbab84f4d9a7?pvs=21)

[cnv to netcdf conversion process](https://app.notion.com/p/cnv-to-netcdf-conversion-process-3c09b541ddc88038871ed22e45db3813?pvs=21)
