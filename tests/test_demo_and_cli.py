"""Smoke tests for the headless demo and the CLI wiring."""

from local_flow.app import main
from local_flow.demo import run_demo


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
