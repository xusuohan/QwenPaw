# -*- coding: utf-8 -*-
"""Tests for runner SafeJSONSession async atomic save."""
from __future__ import annotations

import json

from qwenpaw.app.runner.session import SafeJSONSession


class _State:
    def __init__(self, payload):
        self._payload = payload

    def state_dict(self):
        return self._payload


class TestSaveSessionState:
    async def test_save_writes_async_compact_no_tmp_leftover(self, tmp_path):
        saver = SafeJSONSession(save_dir=str(tmp_path))
        await saver.save_session_state(
            "sid1",
            user_id="u",
            channel="c",
            memory=_State({"k": "v"}),
        )
        files = list(tmp_path.rglob("*.json"))
        assert files, "session file not written"
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data == {"memory": {"k": "v"}}
        # compact (indent=None): no pretty-print indentation
        assert "\n  " not in files[0].read_text(encoding="utf-8")
        assert not list(tmp_path.rglob("*.tmp.*"))
