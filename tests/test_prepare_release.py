from scripts import prepare_release


def test_update_readme_supports_d1_first_release_markers(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    readme.write_text(
        """# ContextWeaver

Current package version: **0.18.1**

## Roadmap

| Milestone | Status | Meaning |
|---|---|---|
| **v0.18.1 — D1 survival experiment baseline** | ✅ current (v0.18.1) | Baseline evidence. |
| D1 lower-cost candidate | next if evidence supports it | Follow-up work. |

### Project comparison

| Project | Release |
|---|---|
| ContextWeaver (this repo, [v0.18.1](https://github.com/dgenio/contextweaver/releases/tag/v0.18.1)) | current package release |
""",
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
    assert (
        "[v0.18.2](https://github.com/dgenio/contextweaver/releases/tag/v0.18.2)" in updated
    )
    assert "| **v0.18.1 — D1 survival experiment baseline** | ✅ complete |" in updated
    assert (
        "| **v0.18.2** | ✅ current (v0.18.2) | "
        "D1 survival experiment and release-path recovery |"
    ) in updated
