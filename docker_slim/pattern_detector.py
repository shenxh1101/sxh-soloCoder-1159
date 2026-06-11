import fnmatch
import os
from typing import Optional
from docker_slim.layer_analyzer import LayerAnalysis, LayerDiff
from docker_slim.utils import KNOWN_CACHE_PATTERNS, format_size, normalize_path, matches_any_glob


class PatternIssue:
    def __init__(self, pattern_type: str, severity: str, description: str,
                 layer_index: int, size_estimate: int, details: list):
        self.pattern_type = pattern_type
        self.severity = severity
        self.description = description
        self.layer_index = layer_index
        self.size_estimate = size_estimate
        self.details = details

    def __repr__(self):
        return f"PatternIssue(type={self.pattern_type}, severity={self.severity}, size={format_size(self.size_estimate)})"


class PatternDetector:
    def __init__(self, analysis: LayerAnalysis):
        self.analysis = analysis
        self.issues = []

    def detect_all(self) -> list:
        self.issues = []
        self._detect_cache_files()
        self._detect_add_delete_in_cross_layer()
        self._detect_add_delete_in_same_layer()
        self._detect_duplicate_files_across_layers()
        self._detect_large_packages()
        self._detect_unnecessary_files()
        self.issues.sort(key=lambda x: x.size_estimate, reverse=True)
        return self.issues

    def _detect_cache_files(self):
        for i, diff in enumerate(self.analysis.layer_diffs):
            cache_matches = []
            total_cache_size = 0
            for path, size in diff.added.items():
                if self._matches_cache_pattern(path):
                    cache_matches.append((path, size))
                    total_cache_size += size

            if cache_matches:
                self.issues.append(PatternIssue(
                    pattern_type="package_cache",
                    severity="high",
                    description=f"Package manager cache files detected in Layer {i}",
                    layer_index=i,
                    size_estimate=total_cache_size,
                    details=sorted(cache_matches, key=lambda x: x[1], reverse=True)[:20],
                ))

    def _matches_cache_pattern(self, path: str) -> bool:
        norm = normalize_path(path)
        return matches_any_glob(norm, KNOWN_CACHE_PATTERNS)

    def _detect_add_delete_in_same_layer(self):
        pass

    def _detect_add_delete_in_cross_layer(self):
        previous_files_by_layer = []
        total_waste = 0
        details = []

        for i, diff in enumerate(self.analysis.layer_diffs):
            for prev_i, prev_added in enumerate(previous_files_by_layer):
                overlap = set(prev_added.keys()) & diff.deleted
                if overlap:
                    waste = sum(prev_added[p] for p in overlap)
                    total_waste += waste
                    if len(details) < 20:
                        wasted_paths = sorted(overlap)[:5]
                        details.append((prev_i, i, waste, wasted_paths))

            previous_files_by_layer.append(diff.added)

        if total_waste > 0:
            self.issues.append(PatternIssue(
                pattern_type="add_delete_cross_layer",
                severity="high",
                description=f"Files added then deleted across layers ({len(details)} occurrences) - consider merging layers",
                layer_index=-1,
                size_estimate=total_waste,
                details=details,
            ))

    def _detect_add_delete_in_same_layer(self):
        pass

    def _detect_duplicate_files_across_layers(self):
        file_locations = {}
        for i, diff in enumerate(self.analysis.layer_diffs):
            for path, size in diff.added.items():
                if path not in file_locations:
                    file_locations[path] = []
                file_locations[path].append((i, size))

        duplicate_total = 0
        duplicate_details = []
        for path, occurrences in file_locations.items():
            if len(occurrences) > 1:
                sizes = [s for _, s in occurrences]
                if len(set(sizes)) > 1:
                    continue
                dup_size = occurrences[-1][1]
                duplicate_total += dup_size
                layers_list = [f"Layer {idx}" for idx, _ in occurrences]
                duplicate_details.append(
                    (path, dup_size, ", ".join(layers_list))
                )

        if duplicate_details:
            sorted_details = sorted(duplicate_details, key=lambda x: x[1], reverse=True)[:20]
            self.issues.append(PatternIssue(
                pattern_type="duplicate_across_layers",
                severity="medium",
                description=f"Files duplicated across multiple layers",
                layer_index=-1,
                size_estimate=duplicate_total,
                details=sorted_details,
            ))

    def _detect_large_packages(self):
        for i, diff in enumerate(self.analysis.layer_diffs):
            large_files = [(p, s) for p, s in diff.added.items() if s > 10 * 1024 * 1024]
            if large_files:
                large_total = sum(s for _, s in large_files)
                self.issues.append(PatternIssue(
                    pattern_type="large_files",
                    severity="info",
                    description=f"Large files (>10MB) detected in Layer {i}",
                    layer_index=i,
                    size_estimate=large_total,
                    details=sorted(large_files, key=lambda x: x[1], reverse=True)[:10],
                ))

    def _detect_unnecessary_files(self):
        unnecessary_patterns = [
            "**/*.md",
            "**/README*",
            "**/CHANGELOG*",
            "**/LICENSE*",
            "**/COPYING*",
            "**/*.a",
            "**/*.la",
            "**/include/**",
            "**/pkgconfig/**",
        ]
        for i, diff in enumerate(self.analysis.layer_diffs):
            matched = []
            total = 0
            for path, size in diff.added.items():
                for up in unnecessary_patterns:
                    if matches_any_glob(normalize_path(path), [up]):
                        matched.append((path, size))
                        total += size
                        break
            if matched:
                self.issues.append(PatternIssue(
                    pattern_type="unnecessary_files",
                    severity="low",
                    description=f"Potentially unnecessary files in Layer {i}",
                    layer_index=i,
                    size_estimate=total,
                    details=sorted(matched, key=lambda x: x[1], reverse=True)[:10],
                ))