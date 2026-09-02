from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ErddapConfig:
    output_xml: Path
    filedir_prefix: str
    dataset_id_prefix: str = ""
    required_global_attributes: tuple[str, ...] = ()
    global_add_attributes: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        data: object,
        base_dir: Path,
    ) -> "ErddapConfig":
        if not isinstance(data, Mapping):
            raise ValueError("dataset profile must define an erddap JSON object")
        required_keys = {
            "output_xml",
            "filedir_prefix",
            "dataset_id_prefix",
            "required_global_attributes",
            "global_add_attributes",
        }
        missing = sorted(required_keys - set(data))
        unknown = sorted(set(data) - required_keys)
        if missing or unknown:
            details = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if unknown:
                details.append(f"unknown: {', '.join(unknown)}")
            raise ValueError(f"erddap configuration keys are invalid ({'; '.join(details)})")

        output_value = data["output_xml"]
        if not isinstance(output_value, str) or not output_value.strip():
            raise ValueError("erddap.output_xml must be a non-empty path string")
        output_xml = Path(output_value).expanduser()
        if not output_xml.is_absolute():
            output_xml = base_dir / output_xml
        filedir_prefix = data["filedir_prefix"]
        dataset_id_prefix = data["dataset_id_prefix"]
        required_attributes = data["required_global_attributes"]
        additions = data["global_add_attributes"]
        if not isinstance(filedir_prefix, str) or not filedir_prefix.startswith("/"):
            raise ValueError("erddap.filedir_prefix must be an absolute server path")
        if not isinstance(dataset_id_prefix, str):
            raise ValueError("erddap.dataset_id_prefix must be a string")
        if isinstance(required_attributes, (str, bytes)) or not isinstance(required_attributes, Sequence):
            raise ValueError("erddap.required_global_attributes must be a JSON array")
        if not all(isinstance(name, str) and name.strip() for name in required_attributes):
            raise ValueError("erddap.required_global_attributes must contain non-empty strings")
        if not isinstance(additions, Mapping) or not all(
            isinstance(name, str) and name for name in additions
        ):
            raise ValueError("erddap.global_add_attributes must be a JSON object")
        return cls(
            output_xml=output_xml,
            filedir_prefix=filedir_prefix.rstrip("/"),
            dataset_id_prefix=dataset_id_prefix,
            required_global_attributes=tuple(required_attributes),
            global_add_attributes=dict(additions),
        )
