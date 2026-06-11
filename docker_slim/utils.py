import os
import stat


def format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024*1024*1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024*1024):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} B"


def is_symlink(tar_info) -> bool:
    return tar_info.islnk() or tar_info.issym()


def is_dir(tar_info) -> bool:
    return tar_info.isdir()


def is_file(tar_info) -> bool:
    return tar_info.isfile()


def normalize_path(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/").lstrip("/")


DIFF_ADDED = "A"
DIFF_MODIFIED = "M"
DIFF_DELETED = "D"

KNOWN_CACHE_PATTERNS = [
    "/var/cache/apt",
    "/var/lib/apt/lists",
    "/var/cache/yum",
    "/var/cache/dnf",
    "/var/cache/pacman",
    "/var/cache/zypp",
    "/var/cache/apk",
    "/root/.cache",
    "/home/*/.cache",
    "/tmp",
    "/var/tmp",
    "/var/log",
    "/usr/share/doc",
    "/usr/share/man",
    "/usr/share/info",
    "/usr/share/locale",
    "/usr/lib/python*/**/__pycache__",
    "/usr/local/lib/python*/**/__pycache__",
    "**/__pycache__",
    "**/*.pyc",
    "**/*.pyo",
    "/var/cache/ldconfig",
    "/usr/share/zoneinfo",
    "/usr/lib/gcc/**/include",
]

DEFAULT_EXCLUDE_PATTERNS = [
    "/proc",
    "/sys",
    "/dev",
]