import fnmatch
import json
import os
import re
import subprocess
import tarfile
import tempfile
import shutil
from typing import Optional
from docker_slim.utils import normalize_path, is_dir, is_file, is_symlink, matches_any_glob

WHITEOUT_PREFIX = ".wh."
WHITEOUT_OPAQUE = ".wh..wh..opq"


class LayerInfo:
    def __init__(self, layer_id: str, index: int, size_bytes: int = 0):
        self.layer_id = layer_id
        self.index = index
        self.size_bytes = size_bytes
        self.files = {}
        self.dirs = set()
        self.symlinks = {}
        self.whiteouts = set()
        self.opaque_dirs = set()
        self.created_by = ""
        self.comment = ""

    def add_entry(self, path: str, tar_info, size_bytes: int = 0):
        norm = normalize_path(path)
        basename = os.path.basename(norm)

        if basename == WHITEOUT_OPAQUE:
            parent = os.path.dirname(norm)
            self.opaque_dirs.add(parent if parent else "")
            return

        if basename.startswith(WHITEOUT_PREFIX):
            original_name = basename[len(WHITEOUT_PREFIX):]
            parent_dir = os.path.dirname(norm)
            deleted_path = os.path.join(parent_dir, original_name) if parent_dir else original_name
            deleted_norm = normalize_path(deleted_path)
            self.whiteouts.add(deleted_norm)
            return

        if is_dir(tar_info):
            self.dirs.add(norm)
        elif is_symlink(tar_info):
            self.symlinks[norm] = tar_info.linkname
        elif is_file(tar_info):
            self.files[norm] = size_bytes

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def dir_count(self) -> int:
        return len(self.dirs)

    @property
    def total_size(self) -> int:
        return sum(self.files.values())

    def __repr__(self):
        return f"LayerInfo(id={self.layer_id[:12]}, idx={self.index}, size={self.total_size}, files={self.file_count}, whiteouts={len(self.whiteouts)})"


class ImageManifest:
    def __init__(self):
        self.layers = []
        self.config = {}
        self.image_id = ""
        self.tags = []


class ImageParser:
    def __init__(self, exclude_patterns: Optional[list] = None):
        self.exclude_patterns = exclude_patterns or []

    def _should_exclude(self, path: str) -> bool:
        norm = normalize_path(path)
        return matches_any_glob(norm, self.exclude_patterns)

    def parse_from_tar(self, tar_path: str) -> ImageManifest:
        if not os.path.exists(tar_path):
            raise FileNotFoundError(f"Tar file not found: {tar_path}")

        manifest = ImageManifest()
        extract_dir = tempfile.mkdtemp(prefix="docker_slim_")

        try:
            with tarfile.open(tar_path, "r") as tar:
                tar.extractall(path=extract_dir)

            manifest_path = os.path.join(extract_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                raise ValueError("manifest.json not found in tar archive")

            with open(manifest_path, "r") as f:
                manifest_data = json.load(f)

            if manifest_data:
                first_entry = manifest_data[0]
                manifest.tags = first_entry.get("RepoTags", [])
                layer_files = first_entry.get("Layers", [])

                config_file = first_entry.get("Config", "")
                config_path = os.path.join(extract_dir, config_file)
                if os.path.exists(config_path):
                    with open(config_path, "r") as f:
                        manifest.config = json.load(f)

                for idx, layer_rel_path in enumerate(layer_files):
                    layer_tar_path = os.path.join(extract_dir, layer_rel_path)
                    layer_id = layer_rel_path.split("/")[0]
                    layer_info = self._parse_layer_tar(layer_tar_path, layer_id, idx)
                    manifest.layers.append(layer_info)

            manifest.image_id = manifest.config.get("config", {}).get("Image", "")
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

        return manifest

    def parse_from_docker(self, image_name: str) -> ImageManifest:
        image_tar_path = tempfile.mktemp(suffix=".tar", prefix="docker_slim_")
        try:
            subprocess.run(
                ["docker", "save", "-o", image_tar_path, image_name],
                check=True, capture_output=True, text=True,
            )
            manifest = self.parse_from_tar(image_tar_path)
            manifest.tags = [image_name]
            return manifest
        finally:
            if os.path.exists(image_tar_path):
                os.remove(image_tar_path)

    def _parse_layer_tar(self, tar_path: str, layer_id: str, index: int) -> LayerInfo:
        layer_info = LayerInfo(layer_id, index)

        if not os.path.exists(tar_path):
            return layer_info

        with tarfile.open(tar_path, "r") as tar:
            for member in tar:
                if self._should_exclude(member.name):
                    continue
                layer_info.add_entry(member.name, member, member.size)

        json_path = tar_path.replace("layer.tar", "json")
        if json_path != tar_path and os.path.exists(json_path):
            with open(json_path, "r") as f:
                layer_meta = json.load(f)
                layer_info.created_by = layer_meta.get("created_by", "")
                layer_info.comment = layer_meta.get("comment", "")

        return layer_info