from docker_slim.layer_analyzer import LayerAnalysis
from docker_slim.pattern_detector import PatternDetector, PatternIssue
from docker_slim.optimizer import Optimizer
from docker_slim.tree_visualizer import TreeVisualizer
from docker_slim.utils import format_size


class Report:
    def __init__(self, analysis: LayerAnalysis, image_name: str):
        self.analysis = analysis
        self.image_name = image_name
        self.pattern_detector = PatternDetector(analysis)
        self.optimizer = Optimizer(analysis)
        self.visualizer = TreeVisualizer(analysis)

    def generate_full_report(self) -> str:
        lines = []
        lines.extend(self._header())
        lines.extend(self._summary())
        lines.extend(self._layer_tree())
        lines.extend(self._layer_table())
        lines.extend(self._optimization_issues())
        lines.extend(self._estimated_savings())
        lines.extend(self._footer())
        return "\n".join(lines)

    def _header(self) -> list:
        return [
            "",
            "╔══════════════════════════════════════════════════════════════════════╗",
            "║          Docker Slim - Image Layer Analysis Report                   ║",
            "╚══════════════════════════════════════════════════════════════════════╝",
            f"  Image: {self.image_name}",
            f"  Layers: {len(self.analysis.layers)}",
            f"  Total Size: {format_size(self.analysis.total_size)}",
            "",
        ]

    def _summary(self) -> list:
        lines = ["─" * 70, "  LAYER SUMMARY", "─" * 70]
        for i, diff in enumerate(self.analysis.layer_diffs):
            layer = self.analysis.layers[i]
            pct = (diff.added_size / self.analysis.total_size * 100) if self.analysis.total_size > 0 else 0
            lines.append(
                f"  Layer {i}: +{format_size(diff.added_size)} ({pct:.1f}%) "
                f"| {diff.added_file_count} new files | cum: {format_size(self.analysis.cumulative_sizes[i])}"
            )
        lines.append("")
        return lines

    def _layer_tree(self) -> list:
        return [self.visualizer.render()]

    def _layer_table(self) -> list:
        return [self.visualizer.render_summary_table()]

    def _optimization_issues(self) -> list:
        issues = self.pattern_detector.detect_all()
        if not issues:
            return ["", "✓ No optimization issues detected.", ""]

        lines = ["", "─" * 70, "  OPTIMIZATION ISSUES DETECTED", "─" * 70, ""]
        severity_icons = {"high": "🔴", "medium": "🟡", "low": "🟢", "info": "🔵"}

        for i, issue in enumerate(issues):
            icon = severity_icons.get(issue.severity, "⚪")
            lines.append(f"  [{icon} {issue.severity.upper()}] {issue.description}")
            lines.append(f"      Estimated waste: {format_size(issue.size_estimate)}")

            if issue.layer_index >= 0:
                lines.append(f"      Location: Layer {issue.layer_index}")

            if issue.details:
                lines.append(f"      Top files/directories:")
                for detail in issue.details[:5]:
                    if isinstance(detail, tuple) and len(detail) >= 2:
                        path, size = detail[0], detail[1]
                        lines.append(f"        - {path} ({format_size(size)})")
            lines.append("")

        return lines

    def _estimated_savings(self) -> list:
        savings = self.optimizer.estimate_savings()
        lines = [
            "─" * 70,
            "  ESTIMATED SAVINGS",
            "─" * 70,
            f"  Current size:      {format_size(savings['current_size'])}",
            f"  Estimated savings: {format_size(savings['estimated_savings'])} ({savings['savings_percent']:.1f}%)",
            f"  Estimated new size:{format_size(savings['estimated_new_size'])}",
            "",
            "  Savings by category:",
        ]
        for cat, size in savings["by_category"].items():
            lines.append(f"    - {cat}: {format_size(size)}")
        lines.append("")
        return lines

    def _footer(self) -> list:
        return [
            "╔══════════════════════════════════════════════════════════════════════╗",
            "║  Run 'docker-slim optimize --help' for automated slimming options.  ║",
            "╚══════════════════════════════════════════════════════════════════════╝",
            "",
        ]