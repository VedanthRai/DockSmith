"""
Tests for Docksmithfile parser.
Covers: all 6 instructions, unknown instruction error with line number,
line continuations, comments, blank lines, CMD JSON validation.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import pytest
from docksmith.parser import parse_docksmithfile, ParseError


def write_docksmithfile(content):
    f = tempfile.NamedTemporaryFile(mode="w", suffix="Docksmithfile", delete=False)
    f.write(content)
    f.flush()
    return f.name


# --- Happy path ---

def test_all_six_instructions():
    path = write_docksmithfile(
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "ENV KEY=value\n"
        "COPY src.py /app/src.py\n"
        'CMD ["/bin/sh"]\n'
        "RUN echo hello\n"
    )
    instrs = parse_docksmithfile(path)
    names = [i.name for i in instrs]
    assert names == ["FROM", "WORKDIR", "ENV", "COPY", "CMD", "RUN"]


def test_from_with_tag():
    path = write_docksmithfile("FROM alpine:3.18\n")
    instrs = parse_docksmithfile(path)
    assert instrs[0].args == {"image": "alpine", "tag": "3.18"}


def test_from_without_tag_defaults_to_latest():
    path = write_docksmithfile("FROM alpine\n")
    instrs = parse_docksmithfile(path)
    assert instrs[0].args == {"image": "alpine", "tag": "latest"}


def test_workdir():
    path = write_docksmithfile("FROM x\nWORKDIR /app\n")
    instrs = parse_docksmithfile(path)
    assert instrs[1].args == {"path": "/app"}


def test_env_key_value():
    path = write_docksmithfile("FROM x\nENV FOO=bar\n")
    instrs = parse_docksmithfile(path)
    assert instrs[1].args == {"key": "FOO", "value": "bar"}


def test_env_quoted_value():
    path = write_docksmithfile('FROM x\nENV MSG="hello world"\n')
    instrs = parse_docksmithfile(path)
    assert instrs[1].args["value"] == "hello world"


def test_copy_single_src():
    path = write_docksmithfile("FROM x\nCOPY app.py /app/app.py\n")
    instrs = parse_docksmithfile(path)
    assert instrs[1].args == {"srcs": ["app.py"], "dest": "/app/app.py"}


def test_copy_multiple_srcs():
    path = write_docksmithfile("FROM x\nCOPY a.py b.py /app/\n")
    instrs = parse_docksmithfile(path)
    assert instrs[1].args["srcs"] == ["a.py", "b.py"]
    assert instrs[1].args["dest"] == "/app/"


def test_cmd_json_array():
    path = write_docksmithfile('FROM x\nCMD ["/bin/sh", "-c", "echo hi"]\n')
    instrs = parse_docksmithfile(path)
    assert instrs[1].args["cmd"] == ["/bin/sh", "-c", "echo hi"]


def test_run_raw_command():
    path = write_docksmithfile("FROM x\nRUN echo hello world\n")
    instrs = parse_docksmithfile(path)
    assert instrs[1].args == {"command": "echo hello world"}


def test_comments_and_blank_lines_ignored():
    path = write_docksmithfile(
        "# this is a comment\n"
        "\n"
        "FROM alpine:3.18\n"
        "\n"
        "# another comment\n"
        "RUN echo hi\n"
    )
    instrs = parse_docksmithfile(path)
    assert len(instrs) == 2
    assert instrs[0].name == "FROM"
    assert instrs[1].name == "RUN"


def test_line_continuation():
    path = write_docksmithfile(
        "FROM alpine\n"
        "RUN echo \\\n"
        "    hello\n"
    )
    instrs = parse_docksmithfile(path)
    assert "hello" in instrs[1].args["command"]


# --- Error cases ---

def test_unknown_instruction_raises_with_line_number():
    path = write_docksmithfile("FROM alpine\nEXPOSE 8080\n")
    with pytest.raises(ParseError) as exc:
        parse_docksmithfile(path)
    assert "EXPOSE" in str(exc.value)
    # line number must be present
    assert any(c.isdigit() for c in str(exc.value))


def test_cmd_non_json_raises():
    path = write_docksmithfile("FROM x\nCMD /bin/sh\n")
    with pytest.raises(ParseError) as exc:
        parse_docksmithfile(path)
    assert "JSON" in str(exc.value)


def test_cmd_json_object_raises():
    path = write_docksmithfile('FROM x\nCMD {"key": "val"}\n')
    with pytest.raises(ParseError) as exc:
        parse_docksmithfile(path)
    assert "JSON array" in str(exc.value)


def test_env_missing_equals_raises():
    path = write_docksmithfile("FROM x\nENV NOEQUALS\n")
    with pytest.raises(ParseError):
        parse_docksmithfile(path)


def test_copy_missing_dest_raises():
    path = write_docksmithfile("FROM x\nCOPY onlyone\n")
    with pytest.raises(ParseError):
        parse_docksmithfile(path)


def test_from_missing_image_raises():
    path = write_docksmithfile("FROM\n")
    with pytest.raises(ParseError):
        parse_docksmithfile(path)


def test_instruction_case_insensitive():
    # Instructions should be uppercased internally
    path = write_docksmithfile("from alpine\nrun echo hi\n")
    instrs = parse_docksmithfile(path)
    assert instrs[0].name == "FROM"
    assert instrs[1].name == "RUN"
