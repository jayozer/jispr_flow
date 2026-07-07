"""Smoke tests for the headless demo and the CLI wiring."""

import re

from local_flow.app import main
from local_flow.demo import run_demo
from local_flow.history.store import HistoryStore


class TestDemo:
    def test_demo_exits_zero(self, capsys):
        assert run_demo() == 0
        out = capsys.readouterr().out
        assert "rough (mock ASR)" in out
        assert "final inserted text" in out
        assert "Command mode" in out
        assert "demo completed successfully" in out

    def test_demo_via_cli(self, capsys):
        assert main(["demo"]) == 0
        assert "demo completed successfully" in capsys.readouterr().out


class TestCli:
    def test_no_args_prints_help(self, capsys):
        assert main([]) == 0
        assert "local-flow" in capsys.readouterr().out

    def test_polish_no_llm(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(
            ["polish", "--no-llm", "um send the uh report, scratch that, send the invoice"]
        )
        assert code == 0
        assert capsys.readouterr().out.strip() == "send the invoice"

    def test_command_with_mock_llm(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["command", "shout it", "--text", "hello", "--mock"])
        assert code == 0
        # The mock echoes the user prompt; wiring is what we verify here.
        out = capsys.readouterr().out
        assert "hello" in out

    def test_command_against_down_lmstudio_fails_helpfully(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        # Point at a port that is almost certainly closed.
        monkeypatch.setenv("LOCAL_FLOW_LMSTUDIO_BASE_URL", "http://127.0.0.1:59999/v1")
        monkeypatch.setenv("LOCAL_FLOW_LMSTUDIO_MODEL", "some-model")
        monkeypatch.setenv("LOCAL_FLOW_LMSTUDIO_TIMEOUT", "2")
        code = main(["command", "x", "--text", "y"])
        assert code == 1
        err = capsys.readouterr().err
        assert "LM Studio" in err
        assert "hint" in err


class TestHistoryCommand:
    def test_empty_history_prints_friendly_message_with_path(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        code = main(["history"])
        assert code == 0
        out = capsys.readouterr().out
        assert str(tmp_path / "history.jsonl") in out

    def test_lists_newest_first_with_truncation(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="one rough", final="One.", used_llm=False)
        long_final = "x" * 100
        store.append_new(rough="two rough", final=long_final, used_llm=True)

        code = main(["history"])
        assert code == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2
        # newest first: the long/llm record comes before the first one.
        assert "[llm]" in lines[0]
        assert ("x" * 80 + "...") in lines[0]
        assert "x" * 81 not in lines[0]
        assert "[raw]" in lines[1]
        assert '"One."' in lines[1]

    def test_search_filters_records(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="send report", final="Send the report.")
        store.append_new(rough="jispr flow rocks", final="JiSpr Flow rocks.")

        code = main(["history", "--search", "jispr"])
        assert code == 0
        out = capsys.readouterr().out
        assert "JiSpr Flow rocks." in out
        assert "Send the report." not in out

    def test_verbose_shows_rough(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="um send the uh report", final="Send the report.")

        code = main(["history", "--verbose"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Send the report." in out
        assert "um send the uh report" in out

        code = main(["history"])
        assert code == 0
        out = capsys.readouterr().out
        assert "um send the uh report" not in out

    def test_limit_caps_results(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        for i in range(5):
            store.append_new(rough=f"r{i}", final=f"F{i}")

        code = main(["history", "--limit", "2"])
        assert code == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2

    def test_clear_removes_file(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="x", final="y")
        assert store.path.exists()

        code = main(["history", "--clear"])
        assert code == 0
        out = capsys.readouterr().out
        assert str(store.path) in out
        assert not store.path.exists()

    def test_disabled_history_still_readable_with_notice(
        self, capsys, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="x", final="Kept text.")

        monkeypatch.setenv("LOCAL_FLOW_HISTORY_ENABLED", "false")
        code = main(["history"])
        assert code == 0
        out = capsys.readouterr().out
        assert "disabled" in out.lower()
        assert "Kept text." in out

    def test_timestamp_displayed_in_whole_seconds(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCAL_FLOW_DATA_DIR", str(tmp_path))
        store = HistoryStore(tmp_path)
        store.append_new(rough="test", final="Test record.")

        code = main(["history"])
        assert code == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 1
        # Timestamp should match format YYYY-MM-DDTHH:MM:SSZ (whole seconds, no microseconds)
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z  \[(llm|raw)\]", lines[0]
        )
