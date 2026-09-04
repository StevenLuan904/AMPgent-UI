"""Static startup contracts that do not start processes or touch the database."""

import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent


class StartupContractTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (SCRIPTS / name).read_text(encoding="utf-8-sig")

    def test_launchers_require_observer_protocol_and_service_versions(self):
        for name in ("dev.ps1", "start-ampgent.ps1"):
            text = self.read(name)
            for required in (
                "ampgent-observer/v2",
                "observer-only-cache-v2",
                "protocol_version",
                "service_version",
                "source_fingerprint",
            ):
                self.assertIn(required, text, f"{name} missing {required}")

    def test_launchers_sort_fingerprint_sources_by_hashtable_key(self):
        """Windows PowerShell 5.1 does not reliably expose hashtable keys as properties."""
        for name in ("dev.ps1", "start-ampgent.ps1"):
            text = self.read(name)
            self.assertIn("Sort-Object { $_['Label'] }", text)
            self.assertNotIn("Sort-Object Label", text)

    def test_dev_only_replaces_a_confirmed_observer_process(self):
        text = self.read("dev.ps1")
        for required in (
            "Get-CimInstance Win32_Process",
            "observer_only:app",
            "Stop-StaleObserverApi",
            "为保护 PostgreSQL、Temporal 与 worker",
        ):
            self.assertIn(required, text)
        self.assertIn("Get-NetTCPConnection", text)


if __name__ == "__main__":
    unittest.main()
