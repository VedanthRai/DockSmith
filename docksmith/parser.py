"""
Docksmithfile parser.
Supported instructions: FROM, COPY, RUN, WORKDIR, ENV, CMD
"""

import json
import re


VALID_INSTRUCTIONS = {"FROM", "COPY", "RUN", "WORKDIR", "ENV", "CMD"}


class ParseError(Exception):
    pass


class Instruction:
    def __init__(self, name, args, line_num, raw_text):
        self.name = name       # e.g. "RUN"
        self.args = args       # parsed args (varies by instruction)
        self.line_num = line_num
        self.raw_text = raw_text  # original full text e.g. "RUN pip install ..."

    def __repr__(self):
        return f"Instruction({self.name}, {self.args!r}, line={self.line_num})"


def parse_docksmithfile(filepath):
    """Parse a Docksmithfile and return a list of Instruction objects."""
    with open(filepath, "r") as f:
        lines = f.readlines()

    instructions = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")
        i += 1

        # Handle line continuations
        while line.endswith("\\"):
            line = line[:-1]
            if i < len(lines):
                line += lines[i].rstrip("\n").lstrip()
                i += 1

        # Strip comments and blank lines
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Split on first whitespace to get instruction name
        parts = stripped.split(None, 1)
        inst_name = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""

        if inst_name not in VALID_INSTRUCTIONS:
            raise ParseError(
                f"Line {i}: Unknown instruction '{inst_name}'. "
                f"Valid instructions are: {', '.join(sorted(VALID_INSTRUCTIONS))}"
            )

        # Parse instruction-specific args
        raw_text = stripped

        if inst_name == "FROM":
            # FROM <image>[:<tag>]
            args = parse_from(rest, i)
        elif inst_name == "COPY":
            # COPY <src> <dest>
            args = parse_copy(rest, i)
        elif inst_name == "RUN":
            # RUN <command>
            args = {"command": rest}
        elif inst_name == "WORKDIR":
            # WORKDIR <path>
            args = {"path": rest.strip()}
        elif inst_name == "ENV":
            # ENV <key>=<value>
            args = parse_env(rest, i)
        elif inst_name == "CMD":
            # CMD ["exec","arg"]
            args = parse_cmd(rest, i)

        instructions.append(Instruction(inst_name, args, i, raw_text))

    return instructions


def parse_from(rest, line_num):
    rest = rest.strip()
    if not rest:
        raise ParseError(f"Line {line_num}: FROM requires an image name")
    if ":" in rest:
        image, tag = rest.rsplit(":", 1)
    else:
        image, tag = rest, "latest"
    return {"image": image, "tag": tag}


def parse_copy(rest, line_num):
    """Parse COPY <src> <dest> with glob support."""
    parts = rest.split()
    if len(parts) < 2:
        raise ParseError(f"Line {line_num}: COPY requires at least src and dest arguments")
    srcs = parts[:-1]
    dest = parts[-1]
    return {"srcs": srcs, "dest": dest}


def parse_env(rest, line_num):
    """Parse ENV key=value."""
    rest = rest.strip()
    if "=" not in rest:
        raise ParseError(f"Line {line_num}: ENV requires key=value format")
    key, value = rest.split("=", 1)
    key = key.strip()
    value = value.strip()
    # Remove surrounding quotes if present
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    return {"key": key, "value": value}


def parse_cmd(rest, line_num):
    """Parse CMD ["exec","arg","..."] - JSON array form required."""
    rest = rest.strip()
    try:
        cmd_list = json.loads(rest)
        if not isinstance(cmd_list, list):
            raise ParseError(f"Line {line_num}: CMD must be a JSON array")
        return {"cmd": cmd_list}
    except json.JSONDecodeError as e:
        raise ParseError(f"Line {line_num}: CMD must be valid JSON array: {e}")
