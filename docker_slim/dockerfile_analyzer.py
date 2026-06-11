import re
from typing import Optional, List
from docker_slim.utils import format_size


class InstructionImpact:
    def __init__(self, line_num: int, instruction: str, arguments: str):
        self.line_num = line_num
        self.instruction = instruction
        self.arguments = arguments
        self.estimated_size_added = 0
        self.layer_index = -1

    @property
    def size_added_str(self) -> str:
        return format_size(self.estimated_size_added)


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

    def estimate_impact(self, instructions: List[InstructionImpact], layer_sizes: dict) -> List[InstructionImpact]:
        layer_idx = 0
        impactful_instructions = ["RUN", "COPY", "ADD"]

        for instr in instructions:
            if instr.instruction in impactful_instructions and layer_idx < len(layer_sizes):
                instr.estimated_size_added = layer_sizes.get(layer_idx, 0)
                instr.layer_index = layer_idx
                layer_idx += 1
            elif instr.instruction == "FROM":
                pass
            else:
                instr.estimated_size_added = 0
        return self.instructions

    def generate_report(self) -> str:
        lines = []
        lines.append("")
        lines.append("─" * 80)
        lines.append("  Dockerfile Instruction Impact Analysis")
        lines.append("─" * 80)
        lines.append("")
        lines.append(f"{'Line':<6} {'Instr':<10} {'Size Added':<12} {'Arguments'}")
        lines.append("─" * 80)

        for instr in self.instructions:
            arg_short = (instr.arguments[:50] + "...") if len(instr.arguments) > 50 else instr.arguments
            size_str = instr.size_added_str if instr.estimated_size_added > 0 else "-"
            lines.append(f"{instr.line_num:<6} {instr.instruction:<10} {size_str:<12} {arg_short}")

        lines.append("")

        if self.instructions:
            large = [i for i in self.instructions if i.estimated_size_added > 10 * 1024 * 1024]
            if large:
                lines.append("📊 Top 5 largest instructions:")
                sorted_large = sorted(large, key=lambda x: x.estimated_size_added, reverse=True)[:5]
                for i, instr in enumerate(sorted_large):
                    lines.append(f"  {i+1}. Line {instr.line_num} [{instr.instruction}]: {instr.size_added_str}")
                    lines.append(f"      → {instr.arguments[:80]}")
                lines.append("")

        lines.append("")
        return "\n".join(lines)