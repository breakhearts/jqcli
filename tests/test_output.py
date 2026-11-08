from __future__ import annotations

import io
import json
import sys

from jqcli.output import write_json, write_json_line


def test_write_json_escapes_text_when_stdout_encoding_rejects_it(monkeypatch):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="gbk")
    monkeypatch.setattr(sys, "stdout", stream)

    write_json({"code": "value²"})
    stream.flush()

    assert json.loads(buffer.getvalue().decode("gbk")) == {"code": "value²"}


def test_write_json_line_preserves_stream_encoding_between_records(monkeypatch):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="gbk")
    monkeypatch.setattr(sys, "stdout", stream)

    write_json_line({"code": "中文"})
    write_json_line({"code": "value²"})

    lines = buffer.getvalue().decode("gbk").splitlines()
    assert lines[0] == '{"code":"中文"}'
    assert [json.loads(line) for line in lines] == [{"code": "中文"}, {"code": "value²"}]
