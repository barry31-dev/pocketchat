"""One closed declaration for every model-callable local tool.

The provider offer, the host validator and the trace summary all derive from
these records. A tool that is not named here cannot be offered, and a result
whose arguments do not match the declared shape never reaches file or memory
I/O.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

MAX_ARGUMENT_BYTES = 64 * 1024
MAX_DIAGNOSTIC_CHARS = 160


class ToolContractError(ValueError):
    """The tool is unknown or its arguments do not match its contract."""


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    parameters: Dict[str, Any]
    required: Tuple[str, ...]
    argument_types: Mapping[str, type]

    @property
    def schema_hash(self) -> str:
        encoded = json.dumps(self.parameters, ensure_ascii=False,
                             sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _path_parameters(extra=None, description="Workspace-relative allowed path"):
    properties = {
        "path": {
            "type": "string",
            "description": description,
        },
    }
    if extra:
        properties.update(extra)
    return {
        "type": "object",
        "properties": properties,
        "required": ["path"] + (["content"] if extra else []),
        "additionalProperties": False,
    }


def _contracts():
    read = ToolContract(
        name="read_file",
        description="Read one explicitly allowed UTF-8 workspace file.",
        parameters=_path_parameters(),
        required=("path",),
        argument_types={"path": str},
    )
    write = ToolContract(
        name="write_file",
        description="Create one new UTF-8 workspace file; never overwrite.",
        parameters=_path_parameters(
            {
                "content": {
                    "type": "string",
                    "description": "UTF-8 file content",
                },
            },
            "Workspace-relative new path",
        ),
        required=("path", "content"),
        argument_types={"path": str, "content": str},
    )
    memory = ToolContract(
        name="memory_search",
        description="Search explicitly saved local memory records.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        required=("query",),
        argument_types={"query": str},
    )
    return {item.name: item for item in (read, write, memory)}


CONTRACTS = _contracts()


def contract_names() -> Tuple[str, ...]:
    return tuple(CONTRACTS)


def provider_tool(name: str) -> Dict[str, Any]:
    """Return a provider tool declaration copied from the host contract."""
    contract = CONTRACTS.get(name)
    if contract is None:
        raise ToolContractError("unknown tool contract %r" % name)
    return {
        "type": "function",
        "function": {
            "name": contract.name,
            "description": contract.description,
            "parameters": json.loads(json.dumps(contract.parameters)),
        },
    }


def parse_arguments(name: str, value: Any) -> Dict[str, str]:
    """Validate one model-supplied argument object without touching I/O."""
    contract = CONTRACTS.get(name)
    if contract is None:
        raise ToolContractError("unknown tool contract %r" % name)
    if not isinstance(value, dict):
        raise ToolContractError("%s arguments must be an object" % name)
    unknown = sorted(key for key in value if key not in contract.argument_types)
    if unknown:
        shown = ", ".join(str(key).encode(
            "ascii", "backslashreplace").decode("ascii")[:64]
            for key in unknown[:4])
        if len(unknown) > 4:
            shown += ", ..."
        message = "%s has unknown argument(s): %s" % (name, shown)
        if len(message) > MAX_DIAGNOSTIC_CHARS:
            message = message[:MAX_DIAGNOSTIC_CHARS - 3] + "..."
        raise ToolContractError(message)
    missing = [key for key in contract.required if key not in value]
    if missing:
        raise ToolContractError("%s is missing argument(s): %s"
                                % (name, ", ".join(missing)))
    for key, item in value.items():
        expected = contract.argument_types[key]
        if not isinstance(item, expected):
            raise ToolContractError("%s.%s must be a string" % (name, key))
        if key in ("path", "query") and not item.strip():
            raise ToolContractError("%s.%s must be a non-empty string"
                                    % (name, key))
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ToolContractError("%s arguments are not JSON: %s" % (name, exc))
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise ToolContractError("%s arguments exceed %d bytes"
                                % (name, MAX_ARGUMENT_BYTES))
    return {key: value[key] for key in contract.required}


def contract_summary(enabled: Mapping[str, bool]) -> list:
    """Describe all three contracts and which ones this profile enabled."""
    result = []
    for name in contract_names():
        contract = CONTRACTS[name]
        result.append({
            "name": name,
            "available": enabled.get(name) is True,
            "schema_hash": contract.schema_hash,
        })
    return result
