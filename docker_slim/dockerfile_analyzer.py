import re
from typing import Optional, List, Dict
from docker_slim.utils import format_size


class InstructionImpact:
    def __init__(self, line_num: int, instruction: str, arguments: str):
        self.line_num = line_num
        self.instruction = instruction
        self.arguments = arguments
        self.estimated_size_added = 0
        self.layer_index = -1
        self.matched = False

    @property
    def size_added_str(self) -> str:
        if self.estimated_size_added == 0 and self.instruction in ("RUN", "COPY", "ADD"):
            return "[UNMATCHED]"
        if self.estimated_size_added > 0:
            return format_size(self.estimated_size_added)
        return "-"

    @property
    def label(self) -> str:
        if self.layer_index >= 0:
            return f"Layer {self.layer_index}"
        if self.instruction in ("RUN", "COPY", "ADD"):
            return "[MISSING]"
        return "-"


class DockerfileAnalyzer:
    def __init__(self, dockerfile_path: str):
        self.path = dockerfile_path
        self.instructions = []

    def parse(self) -> List[InstructionImpact]:
        self.instructions = []
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        current_continuation = False
        current_instr = None
        current_args = []
        current_line = 0

        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if current_continuation:
                if line.endswith("\\"):
                    current_args.append(line[:-1].strip())
                    current_continuation = True
                else:
                    current_args.append(line.strip())
                    full_args = " ".join(current_args)
                    instr = InstructionImpact(current_line, current_instr, full_args)
                    self.instructions.append(instr)
                    current_continuation = False
            else:
                parts = line.split(None, 1)
                if len(parts) == 2:
                    instr_name, args_part = parts
                    if instr_name.upper() in ["FROM", "RUN", "COPY", "ADD", "WORKDIR",
                                             "ENV", "EXPOSE", "ENTRYPOINT", "CMD",
                                              "LABEL", "MAINTAINER", "USER", "VOLUME"]:
                        if args_part.endswith("\\"):
                            current_instr = instr_name.upper()
                            current_args = [args_part[:-1].strip()]
                            current_line = i
                            current_continuation = True
                        else:
                            self.instructions.append(InstructionImpact(i, instr_name.upper(), args_part))

        return self.instructions

    def estimate_impact(self, instructions: List[InstructionImpact], layer_info_list: list, non_base_indices: list = None) -> List[InstructionImpact]:
        if not layer_info_list:
            return self.instructions

        impactful_instructions = ["RUN", "COPY", "ADD"]
        impactful_from_instructions = [i for i in self.instructions if i.instruction in impactful_instructions]

        if non_base_indices and len(non_base_indices) > 0:
            use_layers = [layer_info_list[i] for i in non_base_indices]
            match_count = min(len(impactful_from_instructions), len(use_layers))
            layer_idx = 0
            for instr in self.instructions:
                if instr.instruction in impactful_instructions:
                    if layer_idx < match_count:
                        diff = use_layers[layer_idx]
                        instr.estimated_size_added = diff.added_size
                        instr.layer_index = diff.layer_index
                        instr.matched = True
                        layer_idx += 1
                    else:
                        instr.estimated_size_added = 0
                        instr.layer_index = -1
                        instr.matched = False
                elif instr.instruction == "FROM":
                    instr.matched = True
                else:
                    instr.estimated_size_added = 0
        else:
            impactful_idx = 0
            n_layers = len(layer_info_list)
            for instr in self.instructions:
                if instr.instruction in impactful_instructions:
                    if impactful_idx < n_layers:
                        diff = layer_info_list[impactful_idx]
                        instr.estimated_size_added = diff.added_size
                        instr.layer_index = diff.layer_index
                        instr.matched = True
                        impactful_idx += 1
                    else:
                        instr.estimated_size_added = 0
                        instr.layer_index = -1
                        instr.matched = False
                elif instr.instruction == "FROM":
                    instr.matched = True
                else:
                    instr.estimated_size_added = 0
        return self.instructions

    def generate_report(self) -> str:
        lines = []
        lines.append("")
        lines.append("─" * 95)
        lines.append("  Dockerfile Instruction Impact Analysis")
        lines.append("─" * 95)
        lines.append("")
        lines.append(f"{'Line':<6} {'Instr':<10} {'Layer':<10} {'Size Added':<14} {'Arguments'}")
        lines.append("─" * 95)

        for instr in self.instructions:
            arg_short = (instr.arguments[:55] + "...") if len(instr.arguments) > 55 else instr.arguments
            size_str = instr.size_added_str
            layer_label = instr.label
            lines.append(f"{instr.line_num:<6} {instr.instruction:<10} {layer_label:<10} {size_str:<14} {arg_short}")

        lines.append("")

        unmatched = [i for i in self.instructions if not i.matched and i.instruction in ("RUN", "COPY", "ADD")]
        if unmatched:
            lines.append("⚠️  WARNING: The following instructions could not be matched to image layers:")
            for instr in unmatched:
                lines.append(f"     Line {instr.line_num}: {instr.instruction} {instr.arguments[:60]}")
            lines.append("     This may indicate the Dockerfile and image are out of sync.")
            lines.append("")

        if self.instructions:
            impactful = [i for i in self.instructions if i.estimated_size_added > 0]
            if impactful:
                lines.append("📊 Instruction impact ranking (by size added):")
                sorted_impactful = sorted(impactful, key=lambda x: x.estimated_size_added, reverse=True)[:5]
                for rank, instr in enumerate(sorted_impactful):
                    lines.append(
                        f"  {rank+1}. Line {instr.line_num} [{instr.instruction}] "
                        f"-> Layer {instr.layer_index}: {instr.size_added_str}"
                    )
                    lines.append(f"      → {instr.arguments[:80]}")
                lines.append("")

        lines.append("")
        return "\n".join(lines)