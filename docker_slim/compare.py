from typing import Optional
from docker_slim.layer_analyzer import LayerAnalysis
from docker_slim.utils import format_size


class ComparisonResult:
    def __init__(self):
        self.original_layers = 0
        self.original_size = 0
        self.slimmed_layers = 0
        self.slimmed_size = 0
        self.size_saved = 0
        self.layers_saved = 0
        self.size_percent = 0.0
        self.layers_percent = 0.0


class ComparisonReport:
    def __init__(self, original: LayerAnalysis, slimmed: LayerAnalysis):
        self.original = original
        self.slimmed = slimmed
        self.result = ComparisonResult()
        self._calculate()

    def _calculate(self):
        self.result.original_layers = len(self.original.layers)
        self.result.original_size = self.original.total_size
        self.result.slimmed_layers = len(self.slimmed.layers)
        self.result.slimmed_size = self.slimmed.total_size
        self.result.size_saved = self.result.original_size - self.result.slimmed_size
        self.result.layers_saved = self.result.original_layers - self.result.slimmed_layers

        if self.result.original_size > 0:
            self.result.size_percent = (self.result.size_saved / self.result.original_size * 100)
        if self.result.original_layers > 0:
            self.result.layers_percent = (self.result.layers_saved / self.result.original_layers * 100)

    def generate(self) -> str:
        lines = []
        lines.append("")
        lines.append("╔═════════════════════════════════════════════════════════════════╗")
        lines.append("║            BEFORE vs AFTER Slimming Comparison                   ║")
        lines.append("╚═════════════════════════════════════════════════════════════════╝")
        lines.append("")
        lines.append(f"{'Metric':<20} {'Before':<15} {'After':<15} {'Saved':<15}")
        lines.append("─" * 70)
        lines.append(
            f"{'Size':<20} {format_size(self.result.original_size):<15} "
            f"{format_size(self.result.slimmed_size):<15} "
            f"{format_size(self.result.size_saved)} ({self.result.size_percent:.1f}%)"
        )
        lines.append(
            f"{'Layers':<20} {self.result.original_layers:<15} "
            f"{self.result.slimmed_layers:<15} "
            f"{self.result.layers_saved} ({self.result.layers_percent:.1f}%)"
        )
        lines.append("")
        if self.result.size_saved > 0:
            bars = int(self.result.size_percent / 2)
            bar = "█" * bars + "░" * (50 - bars)
            lines.append(f"Size savings: [{bar}] {self.result.size_percent:.1f}% ({format_size(self.result.size_saved)})")
            if self.result.size_percent > 30:
                lines.append("")
                lines.append("🔥 Great improvement! Your image is much smaller now.")
            elif self.result.size_percent > 10:
                lines.append("")
                lines.append("👍 Good improvement achieved!")
        else:
            lines.append("No size saved. Check your exclude patterns.")

        lines.append("")
        return "\n".join(lines)