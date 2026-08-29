"""Extension to category mapping, with optional user configuration.

The default ruleset is deliberately conservative: anything unrecognised lands in
``other`` rather than being guessed at. A wrongly-guessed category means a file
moved somewhere its owner will never think to look, which is worse than leaving
it where it was.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

OTHER = "other"

DEFAULT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "images": (
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
        "heic",
        "heif",
        "bmp",
        "tiff",
        "tif",
        "svg",
        "ico",
        "raw",
        "cr2",
        "nef",
        "dng",
    ),
    "documents": (
        "pdf",
        "doc",
        "docx",
        "txt",
        "rtf",
        "odt",
        "md",
        "tex",
        "epub",
        "mobi",
        "pages",
    ),
    "spreadsheets": ("xls", "xlsx", "xlsm", "csv", "tsv", "ods", "numbers"),
    "presentations": ("ppt", "pptx", "odp", "key"),
    "videos": ("mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg"),
    "audio": ("mp3", "wav", "flac", "aac", "ogg", "oga", "m4a", "wma", "opus", "aiff"),
    "archives": ("zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "iso", "dmg"),
    "code": (
        "py",
        "pyi",
        "js",
        "mjs",
        "ts",
        "tsx",
        "jsx",
        "html",
        "htm",
        "css",
        "scss",
        "json",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "sh",
        "bash",
        "ps1",
        "sql",
        "c",
        "h",
        "cpp",
        "hpp",
        "cs",
        "java",
        "go",
        "rs",
        "rb",
        "php",
        "swift",
        "kt",
    ),
    "executables": ("exe", "msi", "deb", "rpm", "appimage", "apk", "bat", "cmd", "com"),
    "fonts": ("ttf", "otf", "woff", "woff2", "eot"),
}


class ConfigError(ValueError):
    """Raised when a user-supplied rules file cannot be used as written."""


def _normalise_extension(raw: str) -> str:
    return raw.strip().lstrip(".").lower()


@dataclass(frozen=True)
class Ruleset:
    """Maps a file extension to the category folder it belongs in."""

    by_extension: dict[str, str]

    @classmethod
    def default(cls) -> Ruleset:
        return cls.from_mapping(DEFAULT_CATEGORIES)

    @classmethod
    def from_mapping(cls, mapping: dict[str, tuple[str, ...] | list[str]]) -> Ruleset:
        by_extension: dict[str, str] = {}
        for category, extensions in mapping.items():
            for raw in extensions:
                by_extension[_normalise_extension(raw)] = category
        return cls(by_extension=by_extension)

    @classmethod
    def from_toml(cls, path: Path | str) -> Ruleset:
        """Load a rules file, merging its categories over the defaults.

        Merging happens per extension, so a config that only defines ``images``
        keeps every other default category intact.
        """
        path = Path(path)
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc

        categories = raw.get("categories", {})
        if not isinstance(categories, dict):
            raise ConfigError(f"{path}: [categories] must be a table of name = [extensions]")

        seen: dict[str, str] = {}
        for category, extensions in categories.items():
            if not isinstance(extensions, list):
                raise ConfigError(f"{path}: category '{category}' must be a list of extensions")
            for raw_ext in extensions:
                if not isinstance(raw_ext, str):
                    raise ConfigError(f"{path}: category '{category}' has a non-string extension")
                ext = _normalise_extension(raw_ext)
                if ext in seen and seen[ext] != category:
                    raise ConfigError(
                        f"{path}: extension '{ext}' is claimed by both "
                        f"'{seen[ext]}' and '{category}'"
                    )
                seen[ext] = category

        merged = dict(cls.default().by_extension)
        merged.update(seen)
        return cls(by_extension=merged)

    def category_for(self, path: Path) -> str:
        """Return the category for ``path``, or ``other`` if unrecognised."""
        extension = _normalise_extension(path.suffix)
        if not extension:
            return OTHER
        return self.by_extension.get(extension, OTHER)

    @property
    def category_names(self) -> list[str]:
        return sorted({*self.by_extension.values(), OTHER})


def load_exclusions_from_toml(path: Path | str) -> tuple[str, ...]:
    """Read ``[settings] exclude = [...]`` from a rules file, if present."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    settings = raw.get("settings", {})
    if not isinstance(settings, dict):
        raise ConfigError(f"{path}: [settings] must be a table")
    exclude = settings.get("exclude", [])
    if not isinstance(exclude, list) or any(not isinstance(item, str) for item in exclude):
        raise ConfigError(f"{path}: [settings] exclude must be a list of strings")
    return tuple(exclude)
