from pathlib import Path

import pytest

from scripts import prepare_release


def test_update_readme_supports_d1_first_release_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        (
            "# ContextWeaver\n\n"
            "Current package version: **0.18.1**\n\n"
            "## Roadmap\n\n"
            "| Milestone | Status | Meaning |\n"
            "|---|---|---|\n"
            "| **v0.18.1 — D1 survival experiment baseline** | "
            "✅ current (v0.18.1) | Baseline evidence. |\n"
            "| D1 lower-cost candidate | next if evidence supports it | Follow-up work. |\n\n"
            "### Project comparison\n\n"
            "| Project | Release |\n"
            "|---|---|\n"
            "| ContextWeaver (this repo, "
            "[v0.18.1](https://github.com/dgenio/contextweaver/releases/tag/v0.18.1)) "
            "| current package release |\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(prepare_release, "README", readme)

    prepare_release._update_readme(
        "0.18.1",
        "0.18.2",
        "D1 survival experiment and release-path recovery",
    )

    updated = readme.read_text(encoding="utf-8")
    assert "Current package version: **0.18.2**" in updated
    assert "[v0.18.2](https://github.com/dgenio/contextweaver/releases/tag/v0.18.2)" in updated
    assert "| **v0.18.1 — D1 survival experiment baseline** | ✅ complete |" in updated
    expected_current_row = (
        "| **v0.18.2** | ✅ current (v0.18.2) | "
        "D1 survival experiment and release-path recovery |"
    )
    assert expected_current_row in updated
