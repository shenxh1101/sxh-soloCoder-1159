from docker_slim.image_parser import LayerInfo
from docker_slim.layer_analyzer import LayerAnalysis
from docker_slim.utils import format_size

_TREE_BRANCH = "├── "
_TREE_LAST = "└── "
_TREE_PIPE = "│   "
_TREE_SPACE = "    "

SORT_ADDED = "added"
SORT_CUMULATIVE = "cumulative"
SORT_DELETED = "deleted"


class TreeVisualizer:
    def __init__(self, analysis: LayerAnalysis, sort_by: str = SORT_ADDED, top_n: int = 5):
        self.analysis = analysis
        self.sort_by = sort_by
        self.top_n = top_n
        self.max_bar_width = 40

    def _sort_key(self, i: int):
        diff = self.analysis.layer_diffs[i]
        if self.sort_by == SORT_CUMULATIVE:
            return self.analysis.cumulative_sizes[i]
        elif self.sort_by == SORT_DELETED:
            deleted_size = 0
            for p in diff.deleted:
                for prev_diff in self.analysis.layer_diffs[:i]:
                    if p in prev_diff.added:
                        deleted_size += prev_diff.added[p]
                        break
                    if p in prev_diff.modified:
                        deleted_size += prev_diff.modified[p]
                        break
            return deleted_size
        return diff.added_size

    def _sorted_indices(self):
        indices = list(range(len(self.analysis.layer_diffs)))
        indices.sort(key=self._sort_key, reverse=True)
        return indices

    def render(self) -> str:
        lines = []
        sort_labels = {SORT_ADDED: "Added Size", SORT_CUMULATIVE: "Cumulative Size", SORT_DELETED: "Deleted Reclaim"}
        label = sort_labels.get(self.sort_by, "Added Size")
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  Docker Image Layer Tree (Total: {format_size(self.analysis.total_size)}, Sort: {label})")
        lines.append("=" * 70)
        lines.append("")

        sorted_indices = self._sorted_indices()
        for j, i in enumerate(sorted_indices):
            diff = self.analysis.layer_diffs[i]
            layer = self.analysis.layers[i]
            is_last = (j == len(sorted_indices) - 1)

            prefix = _TREE_LAST if is_last else _TREE_BRANCH

            if self.sort_by == SORT_DELETED:
                deleted_size = 0
                for p in diff.deleted:
                    for prev_diff in self.analysis.layer_diffs[:i]:
                        if p in prev_diff.added:
                            deleted_size += prev_diff.added[p]
                            break
                        if p in prev_diff.modified:
                            deleted_size += prev_diff.modified[p]
                            break
                display_size = deleted_size
            elif self.sort_by == SORT_CUMULATIVE:
                display_size = self.analysis.cumulative_sizes[i]
            else:
                display_size = diff.added_size

            layer_label = f"[Layer {i}]"
            if i in self.analysis.non_base_layer_indices:
                layer_label += " [user]"

            percent = (display_size / self.analysis.total_size * 100) if self.analysis.total_size > 0 else 0
            bar = self._make_bar(min(percent, 100))

            cumulative = self.analysis.cumulative_sizes[i]

            if self.sort_by == SORT_DELETED:
                lines.append(
                    f"{prefix}{layer_label}  "
                    f"reclaim: {format_size(display_size)} ({percent:.1f}%) [cum: {format_size(cumulative)}] {bar}"
                )
            else:
                lines.append(
                    f"{prefix}{layer_label}  "
                    f"+{format_size(diff.added_size)} ({percent:.1f}%) [cum: {format_size(cumulative)}] {bar}"
                )

            child_prefix = _TREE_SPACE if is_last else _TREE_PIPE

            lines.append(f"{child_prefix}{_TREE_LAST}Files: {diff.added_file_count} added, "
                         f"{diff.added_dir_count} dirs")
            if diff.deleted:
                parts = []
                if diff.whiteout_count > 0:
                    parts.append(f"{diff.whiteout_count} via whiteout")
                if diff.opaque_count > 0:
                    parts.append(f"{diff.opaque_count} via opaque")
                del_detail = ", ".join(parts)
                lines.append(f"{child_prefix}{_TREE_LAST}Deleted: {diff.deleted_file_count} files "
                             f"({del_detail})")
            lines.append(f"{child_prefix}{_TREE_LAST}Modified: {len(diff.modified)} files "
                         f"({format_size(diff.modified_size)})")

            top_files = sorted(diff.added.items(), key=lambda x: x[1], reverse=True)[:self.top_n]
            if top_files:
                lines.append(f"{child_prefix}{_TREE_LAST}Largest {self.top_n} new files:")
                for k, (path, size) in enumerate(top_files):
                    sp = child_prefix + ("    " if is_last else _TREE_PIPE)
                    file_prefix = _TREE_LAST if k == len(top_files) - 1 else _TREE_BRANCH
                    lines.append(f"{sp}  {file_prefix}{path} ({format_size(size)})")

            deleted_list = sorted(diff.deleted)
            if deleted_list:
                show_del = deleted_list[:self.top_n]
                lines.append(f"{child_prefix}{_TREE_LAST}Top {self.top_n} deleted paths:")
                for k, path in enumerate(show_del):
                    sp = child_prefix + ("    " if is_last else _TREE_PIPE)
                    file_prefix = _TREE_LAST if k == len(show_del) - 1 else _TREE_BRANCH
                    lines.append(f"{sp}  {file_prefix}{path}")

            if diff.created_by:
                lines.append(f"{child_prefix}{_TREE_LAST}Instruction: {diff.created_by.strip()}")

            if not is_last:
                lines.append(f"{child_prefix}")

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    def _make_bar(self, percent: float) -> str:
        filled = int(percent / 100 * self.max_bar_width)
        if filled > self.max_bar_width:
            filled = self.max_bar_width
        bar = "█" * filled + "░" * (self.max_bar_width - filled)
        return f"[{bar}]"

    def render_summary_table(self) -> str:
        lines = []
        lines.append("")
        lines.append("-" * 100)
        header = f"{'Layer':<6} {'Layer ID':<16} {'Added':<14} {'%':<7} {'Cumulative':<14} {'Files':<8} {'Deleted':<8}"
        lines.append(header)
        lines.append("-" * 100)

        sorted_indices = self._sorted_indices()
        for j, i in enumerate(sorted_indices):
            diff = self.analysis.layer_diffs[i]
            layer = self.analysis.layers[i]
            lid = layer.layer_id[:12] if layer.layer_id else "unknown"
            added = format_size(diff.added_size)
            percent = (diff.added_size / self.analysis.total_size * 100) if self.analysis.total_size > 0 else 0
            cumulative = format_size(self.analysis.cumulative_sizes[i])
            files = str(diff.added_file_count)
            deleted = str(diff.deleted_file_count) if diff.deleted else "0"

            lines.append(
                f"{i:<6} {lid:<16} {added:<14} {percent:<6.1f}% {cumulative:<14} {files:<8} {deleted:<8}"
            )

        lines.append("-" * 100)
        lines.append(f"{'Total':<6} {'':16} {format_size(self.analysis.total_size):<14}")
        lines.append("")
        return "\n".join(lines)