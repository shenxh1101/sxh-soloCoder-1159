from typing import Optional
from docker_slim.image_parser import ImageManifest, LayerInfo
from docker_slim.utils import (
    normalize_path, DIFF_ADDED, DIFF_MODIFIED, DIFF_DELETED,
    KNOWN_CACHE_PATTERNS,
)


class LayerAnalysis:
    def __init__(self):
        self.layers = []
        self.layer_diffs = []
        self.total_size = 0
        self.cumulative_sizes = []


class LayerAnalyzer:
    def __init__(self, manifest: ImageManifest):
        self.manifest = manifest

    def analyze(self) -> LayerAnalysis:
        result = LayerAnalysis()
        result.layers = self.manifest.layers

        cumulative_state = {}
        for layer in self.manifest.layers:
            diff = self._compute_layer_diff(cumulative_state, layer)
            result.layer_diffs.append(diff)

            for path, size in diff.added.items():
                cumulative_state[path] = size
            for path in diff.modified:
                cumulative_state[path] = diff.modified[path]
            for path in diff.deleted:
                cumulative_state.pop(path, None)

            cumulative_total = sum(cumulative_state.values())
            result.cumulative_sizes.append(cumulative_total)

        result.total_size = result.cumulative_sizes[-1] if result.cumulative_sizes else 0
        return result

    def _compute_layer_diff(self, previous_state: dict, layer: LayerInfo):
        diff = LayerDiff()
        diff.layer_index = layer.index
        diff.layer_id = layer.layer_id
        diff.created_by = layer.created_by

        for path, size in layer.files.items():
            if path not in previous_state:
                diff.added[path] = size
            elif previous_state[path] != size:
                diff.modified[path] = size
            else:
                diff.unchanged[path] = size

        current_paths = set(layer.files.keys())
        diff.added_dir_count = len(layer.dirs - set(previous_state.keys()))

        return diff


class LayerDiff:
    def __init__(self):
        self.layer_index = 0
        self.layer_id = ""
        self.created_by = ""
        self.added = {}
        self.modified = {}
        self.unchanged = {}
        self.deleted = set()
        self.added_dir_count = 0

    @property
    def added_size(self) -> int:
        return sum(self.added.values())

    @property
    def modified_size(self) -> int:
        return sum(self.modified.values())

    @property
    def total_new_size(self) -> int:
        return self.added_size + self.modified_size

    @property
    def added_file_count(self) -> int:
        return len(self.added)

    def __repr__(self):
        return (
            f"LayerDiff(idx={self.layer_index}, added={len(self.added)} files, "
            f"modified={len(self.modified)} files, added_size={self.added_size})"
        )