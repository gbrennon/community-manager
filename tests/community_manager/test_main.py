from unittest import mock

from community_manager import main


class TestMain:
    """Tests for the main() entry point."""

    def test_main_calls_run(self) -> None:
        with mock.patch("community_manager.run") as mock_run:
            main()
        mock_run.assert_called_once_with()
