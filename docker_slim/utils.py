import fnmatch
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


def matches_any_glob(norm_path: str, patterns: list) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(norm_path, pattern):
            return True
        pattern_no_trailing = pattern.rstrip("/")
        if norm_path == pattern_no_trailing:
            return True
        if norm_path.startswith(pattern_no_trailing + "/"):
            return True
    return False


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
    "**/.npm/_cacache/**",
    "**/.npm_cache/**",
    "**/node_modules/.cache/**",
    "**/.cache/pip/**",
    "**/.pip/cache/**",
    "**/pip-cache/**",
    "/root/.cache/pip",
    "/home/*/.cache/pip",
    "/root/.npm",
    "/root/.npm/_cacache",
    "/root/.cache/yarn",
    "/home/*/.cache/yarn",
    "/usr/local/share/.cache/yarn",
    "/root/.gem",
    "/home/*/.gem",
    "/root/.cargo/registry",
    "/home/*/.cargo/registry",
    "/root/.nuget/packages",
    "/home/*/.nuget/packages",
    "/root/go/pkg/mod",
    "/home/*/go/pkg/mod",
    "/root/.m2/repository",
    "/home/*/.m2/repository",
    "/var/cache/debconf",
    "/var/lib/apt/lists/partial",
]

DEFAULT_EXCLUDE_PATTERNS = [
    "/proc",
    "/sys",
    "/dev",
]