import json
from docker_slim.layer_analyzer import LayerAnalysis
from docker_slim.pattern_detector import PatternDetector, PatternIssue
from docker_slim.optimizer import Optimizer
from docker_slim.tree_visualizer import TreeVisualizer, SORT_ADDED
from docker_slim.dockerfile_analyzer import DockerfileAnalyzer
from docker_slim.utils import format_size, normalize_path

LIFECYCLE_ADDED = "added"
LIFECYCLE_MODIFIED = "modified"
LIFECYCLE_DELETED = "deleted"


class FileLifecycleEvent:
    def __init__(self, path: str, layer_index: int, event_type: str, size_bytes: int = 0):
        self.path = path
        self.layer_index = layer_index
        self.event_type = event_type
        self.size_bytes = size_bytes


class FileLifecycleTracker:
    def __init__(self, analysis: LayerAnalysis):
        self.analysis = analysis
        self.events = {}

    def build(self):
        self.events = {}
        for i, diff in enumerate(self.analysis.layer_diffs):
            for path, size in diff.added.items():
                self._record(path, i, LIFECYCLE_ADDED, size)
            for path, size in diff.modified.items():
                self._record(path, i, LIFECYCLE_MODIFIED, size)
            for path in diff.deleted:
                prev_size = self._get_previous_size(path, i)
                self._record(path, i, LIFECYCLE_DELETED, prev_size)
        return self

    def _record(self, path: str, layer_index: int, event_type: str, size_bytes: int):
        if path not in self.events:
            self.events[path] = []
        self.events[path].append(FileLifecycleEvent(path, layer_index, event_type, size_bytes))

    def _get_previous_size(self, path: str, up_to_layer: int) -> int:
        for li in range(up_to_layer):
            diff = self.analysis.layer_diffs[li]
            if path in diff.added:
                return diff.added[path]
            if path in diff.modified:
                return diff.modified[path]
        return 0

    def get_wasteful_files(self) -> list:
        wasteful = []
        for path, events in self.events.items():
            added_sizes = [e.size_bytes for e in events if e.event_type == LIFECYCLE_ADDED]
            has_deleted = any(e.event_type == LIFECYCLE_DELETED for e in events)
            if has_deleted:
                total_waste = sum(added_sizes)
                if total_waste > 0:
                    wasteful.append((path, total_waste, events))
        wasteful.sort(key=lambda x: x[1], reverse=True)
        return wasteful

    def get_top_churning(self, top_n: int = 20) -> list:
        churn = []
        for path, events in self.events.items():
            if len(events) >= 2:
                sizes = [e.size_bytes for e in events if e.size_bytes > 0]
                total = sum(sizes) if sizes else 0
                churn.append((path, total, len(events), events))
        churn.sort(key=lambda x: x[1], reverse=True)
        return churn[:top_n]


class Report:
    def __init__(self, analysis: LayerAnalysis, image_name: str,
                 sort_by: str = SORT_ADDED, dfa: DockerfileAnalyzer = None):
        self.analysis = analysis
        self.image_name = image_name
        self.sort_by = sort_by
        self.dfa = dfa
        self.pattern_detector = PatternDetector(analysis)
        self.optimizer = Optimizer(analysis)
        self.visualizer = TreeVisualizer(analysis, sort_by=sort_by)
        self.lifecycle = FileLifecycleTracker(analysis).build()

    def generate_full_report(self) -> str:
        lines = []
        lines.extend(self._header())
        lines.extend(self._summary())
        lines.extend(self._layer_tree())
        lines.extend(self._layer_table())
        if self.dfa:
            lines.extend(self._dockerfile_alignment())
        lines.extend(self._file_lifecycle_view())
        lines.extend(self._optimization_issues())
        lines.extend(self._estimated_savings())
        lines.extend(self._footer())
        return "\n".join(lines)

    def generate_json_report(self) -> str:
        issues_data = []
        for issue in self.pattern_detector.detect_all():
            issues_data.append({
                "type": issue.pattern_type,
                "severity": issue.severity,
                "description": issue.description,
                "layer_index": issue.layer_index,
                "size_estimate_bytes": issue.size_estimate,
                "size_estimate": format_size(issue.size_estimate),
                "details": [
                    {
                        "path": d[0] if isinstance(d, tuple) and len(d) >= 1 else str(d),
                        "size_bytes": d[1] if isinstance(d, tuple) and len(d) >= 2 else 0,
                    }
                    for d in issue.details[:20]
                ],
            })

        savings = self.optimizer.estimate_savings()

        layers_data = []
        for i, diff in enumerate(self.analysis.layer_diffs):
            layer = self.analysis.layers[i]
            layers_data.append({
                "index": i,
                "layer_id": layer.layer_id[:12] if layer.layer_id else "",
                "is_user_layer": i in self.analysis.non_base_layer_indices,
                "added_file_count": diff.added_file_count,
                "added_size_bytes": diff.added_size,
                "added_size": format_size(diff.added_size),
                "modified_file_count": len(diff.modified),
                "modified_size_bytes": diff.modified_size,
                "modified_size": format_size(diff.modified_size),
                "deleted_file_count": diff.deleted_file_count,
                "whiteout_count": diff.whiteout_count,
                "opaque_count": diff.opaque_count,
                "cumulative_size_bytes": self.analysis.cumulative_sizes[i],
                "cumulative_size": format_size(self.analysis.cumulative_sizes[i]),
                "top_added_files": [
                    {"path": p, "size_bytes": s, "size": format_size(s)}
                    for p, s in sorted(diff.added.items(), key=lambda x: x[1], reverse=True)[:10]
                ],
                "deleted_paths": sorted(diff.deleted)[:20],
                "created_by": layer.created_by,
            })

        lifecycle_data = []
        for path, waste, events in self.lifecycle.get_wasteful_files()[:30]:
            lifecycle_data.append({
                "path": path,
                "waste_bytes": waste,
                "waste": format_size(waste),
                "events": [
                    {"layer": e.layer_index, "type": e.event_type, "size_bytes": e.size_bytes}
                    for e in events
                ],
            })

        dfa_data = None
        if self.dfa and self.dfa.instructions:
            dfa_data = []
            for instr in self.dfa.instructions:
                dfa_data.append({
                    "line": instr.line_num,
                    "instruction": instr.instruction,
                    "arguments": instr.arguments,
                    "matched": instr.matched,
                    "layer_index": instr.layer_index,
                    "size_added_bytes": instr.estimated_size_added,
                    "size_added": format_size(instr.estimated_size_added) if instr.estimated_size_added > 0 else "-",
                })

        data = {
            "image": self.image_name,
            "total_layers": len(self.analysis.layers),
            "total_size_bytes": self.analysis.total_size,
            "total_size": format_size(self.analysis.total_size),
            "non_base_layer_indices": self.analysis.non_base_layer_indices,
            "layers": layers_data,
            "issues": issues_data,
            "savings": {
                "current_size_bytes": savings["current_size"],
                "current_size": format_size(savings["current_size"]),
                "estimated_savings_bytes": savings["estimated_savings"],
                "estimated_savings": format_size(savings["estimated_savings"]),
                "estimated_new_size_bytes": savings["estimated_new_size"],
                "estimated_new_size": format_size(savings["estimated_new_size"]),
                "savings_percent": round(savings["savings_percent"], 1),
                "by_category": {
                    cat: {"bytes": s, "size": format_size(s)}
                    for cat, s in savings["by_category"].items()
                },
            },
            "file_lifecycle": lifecycle_data,
            "dockerfile_analysis": dfa_data,
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    def _header(self) -> list:
        lines = [
            "",
            "╔══════════════════════════════════════════════════════════════════════╗",
            "║          Docker Slim - Image Layer Analysis Report                   ║",
            "╚══════════════════════════════════════════════════════════════════════╝",
            f"  Image: {self.image_name}",
            f"  Layers: {len(self.analysis.layers)} ({len(self.analysis.non_base_layer_indices)} user)",
            f"  Total Size: {format_size(self.analysis.total_size)}",
            "",
        ]
        return lines

    def _summary(self) -> list:
        lines = ["─" * 70, "  LAYER SUMMARY", "─" * 70]
        for i, diff in enumerate(self.analysis.layer_diffs):
            pct = (diff.added_size / self.analysis.total_size * 100) if self.analysis.total_size > 0 else 0
            tag = ""
            if i in self.analysis.non_base_layer_indices:
                tag = " [user]"
            else:
                tag = " [base]"
            del_info = ""
            if diff.deleted:
                del_info = f" | deleted: {diff.deleted_file_count} files"
            lines.append(
                f"  Layer {i}{tag}: +{format_size(diff.added_size)} ({pct:.1f}%) "
                f"| {diff.added_file_count} new files{del_info} | cum: {format_size(self.analysis.cumulative_sizes[i])}"
            )
        lines.append("")
        return lines

    def _layer_tree(self) -> list:
        return [self.visualizer.render()]

    def _layer_table(self) -> list:
        return [self.visualizer.render_summary_table()]

    def _dockerfile_alignment(self) -> list:
        if not self.dfa or not self.dfa.instructions:
            return []

        lines = ["", "─" * 80, "  Dockerfile <-> Image Layer Alignment (docker history style)", "─" * 80, ""]
        lines.append(f"{'Line':<6} {'Instr':<10} {'Layer':<10} {'Size Added':<14} {'Region':<8} {'Arguments'}")
        lines.append("─" * 80)

        base_count = len(self.analysis.layers) - len(self.analysis.non_base_layer_indices)
        base_used = set()

        for instr in self.dfa.instructions:
            arg_short = (instr.arguments[:45] + "...") if len(instr.arguments) > 45 else instr.arguments
            if instr.instruction == "FROM":
                region = "base"
                base_str = f"Layers 0-{base_count - 1}" if base_count > 0 else "-"
                lines.append(f"{instr.line_num:<6} {instr.instruction:<10} {base_str:<10} {'(inherited)':<14} {region:<8} {arg_short}")
            elif instr.matched and instr.layer_index >= 0:
                region = "user"
                lines.append(f"{instr.line_num:<6} {instr.instruction:<10} Layer {instr.layer_index:<4} {instr.size_added_str:<14} {region:<8} {arg_short}")
            elif instr.instruction in ("RUN", "COPY", "ADD"):
                lines.append(f"{instr.line_num:<6} {instr.instruction:<10} {'[N/A]':<10} {'[UNMATCHED]':<14} {'user':<8} {arg_short}")
            else:
                lines.append(f"{instr.line_num:<6} {instr.instruction:<10} {'-':<10} {'-':<14} {'meta':<8} {arg_short}")

        lines.append("")
        return lines

    def _file_lifecycle_view(self) -> list:
        wasteful = self.lifecycle.get_wasteful_files()
        churning = self.lifecycle.get_top_churning(15)

        lines = ["", "─" * 80, "  FILE LIFECYCLE VIEW", "─" * 80, ""]

        if wasteful:
            lines.append("  Files added then later deleted (wasteful patterns):")
            lines.append(f"  {'Path':<50} {'Waste':<12} {'Lifecycle'}")
            lines.append("  " + "─" * 78)
            for path, waste, events in wasteful[:15]:
                life = " -> ".join(f"L{e.layer_index}:{e.event_type[:3]}" for e in events)
                lines.append(f"  {path:<50} {format_size(waste):<12} {life}")
            lines.append("")

        if churning:
            lines.append("  Files modified across multiple layers (churn):")
            lines.append(f"  {'Path':<50} {'Events':<8} {'Total':<12} {'Lifecycle'}")
            lines.append("  " + "─" * 78)
            for path, total, ev_count, events in churning[:10]:
                life = " -> ".join(f"L{e.layer_index}:{e.event_type[:3]}" for e in events[:5])
                lines.append(f"  {path:<50} {ev_count:<8} {format_size(total):<12} {life}")
            lines.append("")

        if not wasteful and not churning:
            lines.append("  No notable lifecycle events detected.")
            lines.append("")

        return lines

    def _optimization_issues(self) -> list:
        issues = self.pattern_detector.detect_all()
        if not issues:
            return ["", "  No optimization issues detected.", ""]

        lines = ["", "─" * 70, "  OPTIMIZATION ISSUES DETECTED", "─" * 70, ""]
        severity_icons = {"high": "high", "medium": "med", "low": "low", "info": "info"}

        for i, issue in enumerate(issues):
            sev = severity_icons.get(issue.severity, "?")
            lines.append(f"  [{sev.upper()}] {issue.description}")
            lines.append(f"      Estimated waste: {format_size(issue.size_estimate)}")

            if issue.layer_index >= 0:
                lines.append(f"      Location: Layer {issue.layer_index}")

            if issue.details:
                lines.append(f"      Top files/directories:")
                for detail in issue.details[:5]:
                    if isinstance(detail, tuple):
                        if issue.pattern_type == "add_delete_cross_layer" and len(detail) == 4:
                            prev_l, cur_l, waste, paths = detail
                            lines.append(f"        - Layer {prev_l} -> Layer {cur_l}: waste {format_size(waste)} ({', '.join(paths[:3])})")
                        elif len(detail) >= 2:
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