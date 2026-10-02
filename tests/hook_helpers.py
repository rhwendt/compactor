from __future__ import annotations

import io
import json
from typing import Any, Dict, Optional

from compactor.hooks import dispatch
from compactor.state import Hold, State, load, save
from tests.helpers import FIXTURES, NOW, SESSION, TempEnvTestCase, iso_minutes_ago


def fixture_payload(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / "payloads" / name).read_text(encoding="utf-8"))


class HookTestCase(TempEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "350000"

    def run_hook(self, event: str, payload: Optional[Dict[str, Any]] = None,
                 env: Optional[Dict[str, str]] = None, raw: Optional[str] = None):
        if raw is None:
            body: Dict[str, Any] = {"session_id": SESSION, "transcript_path": str(self.transcript_path())}
            body.update(payload or {})
            raw = json.dumps(body)
        out, err = io.StringIO(), io.StringIO()
        code = dispatch(event, stdin=io.StringIO(raw), out=out, err=err,
                        env=self.env if env is None else env, now=NOW)
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def context_of(out: str) -> Optional[str]:
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None

    def hold(self, minutes_ago: float = 10, reason: str = "refactor", **fields: Any) -> None:
        save(SESSION, State(hold=Hold(reason, iso_minutes_ago(minutes_ago)), **fields), self.env)

    def state(self) -> State:
        return load(SESSION, self.env)
