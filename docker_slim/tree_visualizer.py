from docker_slim.image_parser import LayerInfo
from docker_slim.layer_analyzer import LayerAnalysis
from docker_slim.utils import format_size

_TREE_BRANCH = "├── "
_TREE_LAST = "└── "
_TREE_PIPE = "│   "
_TREE_SPACE = "    "
_BAR_CHARS = "█▉▊▋▌▍▎▏"


class TreeVisualizer:
    def __init__(self, analysis: LayerAnalysis):
        self.analysis = analysis
        self.max_bar_width = 40

    def render(self) -> str:
        lines = []
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  Docker Image Layer Tree (Total: {format_size(self.analysis.total_size)})")
        lines.append("=" * 70)
        lines.append("")

        for i, diff in enumerate(self.analysis.layer_diffs):
            layer = self.analysis.layers[i]
            is_last = (i == len(self.analysis.layer_diffs) - 1)

            prefix = _TREE_LAST if is_last else _TREE_BRANCH
            layer_label = f"[Layer {i}]"
            layer_id_short = layer.layer_id[:12] if layer.layer_id else "unknown"

            added_size = diff.added_size
            percent = (added_size / self.analysis.total_size * 100) if self.analysis.total_size > 0 else 0
            bar = self._make_bar(percent)

            cumulative = self.analysis.cumulative_sizes[i]
            cumulative_label = format_size(cumulative)

            lines.append(
                f"{prefix}{layer_label} {layer_id_short}  "
                f"+{format_size(added_size)} ({percent:.1f}%) [{cumulative_label}] {bar}"
            )

            child_prefix = _TREE_SPACE if is_last else _TREE_PIPE
            file_detail_prefix = child_prefix + (_TREE_LAST if is_last else _TREE_BRANCH if True else "")

            lines.append(f"{child_prefix}{_TREE_LAST}Files: {diff.added_file_count} added, "
                         f"{diff.added_dir_count} dirs")
            if diff.deleted:
                lines.append(f"{child_prefix}{_TREE_LAST}Deleted: {diff.deleted_file_count} files "
                             f"removed from previous layers")
            lines.append(f"{child_prefix}{_TREE_LAST}Modified: {len(diff.modified)} files "
                         f"({format_size(diff.modified_size)})")

            top_files = sorted(diff.added.items(), key=lambda x: x[1], reverse=True)[:5]
            if top_files:
                lines.append(f"{child_prefix}{_TREE_LAST}Largest new files:")
                for j, (path, size) in enumerate(top_files):
                    sp = child_prefix + ("    " if is_last else _TREE_PIPE)
                    file_prefix = _TREE_LAST if j == len(top_files) - 1 else _TREE_BRANCH
                    lines.append(f"{sp}  {file_prefix}{path} ({format_size(size)})")

            if diff.deleted and len(diff.deleted) <= 10:
                lines.append(f"{child_prefix}{_TREE_LAST}Deleted files:")
                deleted_list = sorted(diff.deleted)
                for j, path in enumerate(deleted_list):
                    sp = child_prefix + ("    " if is_last else _TREE_PIPE)
                    file_prefix = _TREE_LAST if j == len(deleted_list) - 1 else _TREE_BRANCH
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

        for i, diff in enumerate(self.analysis.layer_diffs):
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