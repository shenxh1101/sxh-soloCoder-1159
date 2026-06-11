import argparse
import sys
import os
from docker_slim import __version__
from docker_slim.image_parser import ImageParser
from docker_slim.layer_analyzer import LayerAnalyzer
from docker_slim.tree_visualizer import TreeVisualizer, SORT_ADDED
from docker_slim.pattern_detector import PatternDetector
from docker_slim.optimizer import Optimizer
from docker_slim.report import Report
from docker_slim.compare import ComparisonReport
from docker_slim.dockerfile_analyzer import DockerfileAnalyzer
from docker_slim.utils import format_size


def cmd_analyze(args):
    print(f"\n  Analyzing image: {args.image}")
    print(f"  Source: {'tar file' if args.tar else 'local Docker'}\n")

    exclude = args.exclude or []
    if args.exclude_node_modules:
        exclude.append("**/node_modules")

    parser = ImageParser(exclude_patterns=exclude)

    try:
        if args.tar:
            manifest = parser.parse_from_tar(args.image)
        else:
            manifest = parser.parse_from_docker(args.image)
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    analyzer = LayerAnalyzer(manifest)
    analysis = analyzer.analyze()

    dfa = None
    if args.dockerfile:
        dfa = DockerfileAnalyzer(args.dockerfile)
        dfa.parse()
        dfa.estimate_impact(dfa.instructions, analysis.layer_diffs, analysis.non_base_layer_indices)

    sort_by_val = getattr(args, "sort_by", None) or SORT_ADDED
    sort_labels = {"added": SORT_ADDED, "cumulative": "cumulative", "deleted": "deleted"}
    sort_by = sort_labels.get(sort_by_val, SORT_ADDED)

    if args.report:
        report = Report(analysis, args.image, sort_by=sort_by, dfa=dfa)
        full_report = report.generate_full_report()
        print(full_report)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(full_report)
            print(f"  Report saved to: {args.output}")

        if args.json is not None:
            json_output = report.generate_json_report()
            if args.json == "__AUTO__":
                json_path = f"{args.image.replace(':', '_').replace('/', '_')}_analysis.json"
            else:
                json_path = args.json
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(json_output)
            print(f"  JSON report saved to: {json_path}")
    else:
        visualizer = TreeVisualizer(analysis, sort_by=sort_by)
        print(visualizer.render())
        print(visualizer.render_summary_table())

        if dfa:
            print(dfa.generate_report())


def cmd_optimize(args):
    print(f"\n  Optimizing image: {args.image}")
    print(f"  Source: {'tar file' if args.tar else 'local Docker'}\n")

    exclude = args.exclude or []
    if args.exclude_node_modules:
        exclude.append("**/node_modules")

    parser = ImageParser(exclude_patterns=exclude)

    try:
        if args.tar:
            manifest = parser.parse_from_tar(args.image)
        else:
            manifest = parser.parse_from_docker(args.image)
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    analyzer = LayerAnalyzer(manifest)
    analysis = analyzer.analyze()

    optimizer = Optimizer(analysis, exclude_patterns=exclude)
    savings = optimizer.estimate_savings()

    print(f"  Current size: {format_size(savings['current_size'])}")
    print(f"  Estimated savings: {format_size(savings['estimated_savings'])} ({savings['savings_percent']:.1f}%)")
    print(f"  Estimated new size: {format_size(savings['estimated_new_size'])}")
    print()

    output_path = args.output or "Dockerfile.slim"
    output_tag = args.output_tag or ""

    if args.full_export:
        if args.tar:
            print("  ERROR: Full export/cleanup/rebuild is only supported when using a local Docker image, not a tar file.")
            print("  Hint: You can import it into Docker first with docker load -i image.tar, then run optimize again.")
            sys.exit(1)

        if args.dry_run:
            print("  [Dry run] Previewing full export/cleanup/rebuild pipeline:")
            print("")
            script = optimizer.generate_export_slim_script(args.image, output_path, output_tag)
            print(script)
            print()
            print("  [Dry run done]")
            return

        script_content = optimizer.generate_export_slim_script(args.image, output_path, output_tag)
        script_path = "docker-slim-export.sh"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        os.chmod(script_path, 0o755)
        print(f"  Full export/cleanup/rebuild script generated: {script_path}")
        print(f"  Dockerfile: {output_path}")
        print()
        print("  To run the automated slimming:")
        print(f"    ./docker-slim-export.sh")
        return

    dockerfile = optimizer.generate_optimized_dockerfile(args.image)

    if not args.dry_run:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(dockerfile)
        print(f"  Optimized Dockerfile saved to: {output_path}")

        if args.build_script:
            script_path = args.build_script
            lines = []
            lines.append("#!/bin/bash")
            lines.append(f"# Build slimmed image from {args.image}")
            lines.append(f"")
            lines.append(f"docker build -f {output_path} -t {args.image}-slim .")
            lines.append(f"echo 'Built: {args.image}-slim'")
            lines.append(f"docker images {args.image}-slim")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.chmod(script_path, 0o755)
            print(f"  Build script saved to: {script_path}")

    print()
    if not args.dry_run:
        print("  To build the slimmed image, run:")
        print(f"    docker build -f {output_path} -t {args.image}-slim .")
    else:
        print("  [Dry run] No files were written out.")
        if output_path:
            print("  Dockerfile preview:")
            print("")
            print(dockerfile)


def cmd_compare(args):
    print(f"\n  Comparing images: {args.original} vs {args.slimmed}\n")

    exclude = args.exclude or []
    parser = ImageParser(exclude_patterns=exclude)

    try:
        manifest_orig = parser.parse_from_docker(args.original)
    except Exception as e:
        print(f"  Error loading original image '{args.original}': {e}")
        sys.exit(1)

    try:
        manifest_slim = parser.parse_from_docker(args.slimmed)
    except Exception as e:
        print(f"  Error loading slimmed image '{args.slimmed}': {e}")
        sys.exit(1)

    analyzer_orig = LayerAnalyzer(manifest_orig)
    analysis_orig = analyzer_orig.analyze()

    analyzer_slim = LayerAnalyzer(manifest_slim)
    analysis_slim = analyzer_slim.analyze()

    comparison = ComparisonReport(analysis_orig, analysis_slim)
    print(comparison.generate())

    if args.verbose:
        print("  Original layers detail:")
        tree_orig = TreeVisualizer(analysis_orig)
        print(tree_orig.render_summary_table())
        print()
        print("  Slimmed layers detail:")
        tree_slim = TreeVisualizer(analysis_slim)
        print(tree_slim.render_summary_table())


def cmd_dockerfile(args):
    if not os.path.exists(args.dockerfile):
        print(f"  Error: Dockerfile not found: {args.dockerfile}")
        sys.exit(1)

    dfa = DockerfileAnalyzer(args.dockerfile)
    dfa.parse()
    dfa.generate_report()

    if args.image:
        print(f"\n  Analyzing corresponding image: {args.image}")
        exclude = args.exclude or []
        parser = ImageParser(exclude_patterns=exclude)
        try:
            manifest = parser.parse_from_docker(args.image)
            analyzer = LayerAnalyzer(manifest)
            analysis = analyzer.analyze()
            dfa.estimate_impact(dfa.instructions, analysis.layer_diffs, analysis.non_base_layer_indices)
            print(dfa.generate_report())
        except Exception as e:
            print(f"  Warning: Could not analyze image: {e}")
            print("  Dockerfile instruction analysis only:")
            print(dfa.generate_report())
    else:
        print(dfa.generate_report())

    if args.output:
        output_path = args.output or "Dockerfile.optimized"
        dfa_optimizer = DockerfileAnalyzer(args.dockerfile)
        dfa_optimizer.parse()
        suggestions = []
        suggestions.append("# Optimized Dockerfile suggestions:")
        suggestions.append("# Combine RUN commands to reduce layers:")
        suggestions.append("#   RUN apt-get update && apt-get install -y pkg1 pkg2 && rm -rf /var/lib/apt/lists/*")
        suggestions.append("#   RUN pip install --no-cache-dir pkg1 pkg2")
        suggestions.append("")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(suggestions))
        print(f"  Optimization suggestions saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        prog="docker-slim",
        description="Docker Image Layer Analysis & Slimming Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  docker-slim analyze myimage:latest
  docker-slim analyze myimage:latest --report
  docker-slim analyze myimage:latest --exclude "**/node_modules"
  docker-slim analyze myimage.tar --tar
  docker-slim optimize myimage:latest
  docker-slim optimize myimage:latest --output Dockerfile.slim
  docker-slim compare myimage:latest myimage:slim
  docker-slim dockerfile Dockerfile --image myimage:latest
        """,
    )

    parser.add_argument("--version", action="version", version=f"docker-slim v{__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    p_analyze = subparsers.add_parser("analyze", help="Analyze a Docker image's layers")
    p_analyze.add_argument("image", help="Docker image name or tar file path")
    p_analyze.add_argument("--tar", action="store_true", help="Treat image argument as tar file path")
    p_analyze.add_argument("--report", "-r", action="store_true", help="Generate full optimization report")
    p_analyze.add_argument("--output", "-o", help="Output file path for report")
    p_analyze.add_argument("--exclude", "-e", nargs="+", help="Exclude files/directories (glob patterns)")
    p_analyze.add_argument("--exclude-node-modules", "--no-node-modules", action="store_true",
                           help="Exclude node_modules directories")
    p_analyze.add_argument("--dockerfile", "-f", help="Analyze Dockerfile alongside image")
    p_analyze.add_argument("--sort-by", choices=["added", "cumulative", "deleted"], default="added",
                           help="Sort tree view by added size / cumulative size / deleted reclaim (default: added)")
    p_analyze.add_argument("--json", nargs="?", const="__AUTO__", default=None,
                           help="Export JSON report (optional: specify output path)")
    p_analyze.set_defaults(func=cmd_analyze)

    p_optimize = subparsers.add_parser("optimize", help="Generate optimized Dockerfile")
    p_optimize.add_argument("image", help="Docker image name or tar file path")
    p_optimize.add_argument("--tar", action="store_true", help="Treat image argument as tar file path")
    p_optimize.add_argument("--output", "-o", help="Output optimized Dockerfile path (default: Dockerfile.slim)")
    p_optimize.add_argument("--output-tag", help="Output image tag for slimmed image (default: image:slim)")
    p_optimize.add_argument("--build-script", help="Generate a build script at given path")
    p_optimize.add_argument("--dry-run", action="store_true", help="Analyze only, do not generate files")
    p_optimize.add_argument("--exclude", "-e", nargs="+", help="Exclude files/directories (glob patterns)")
    p_optimize.add_argument("--exclude-node-modules", "--no-node-modules", action="store_true",
                            help="Exclude node_modules directories")
    p_optimize.add_argument("--full-export", action="store_true",
                            help="Generate a 4-stage export/cleanup/rebuild pipeline script")
    p_optimize.set_defaults(func=cmd_optimize)

    p_compare = subparsers.add_parser("compare", help="Compare original and slimmed images")
    p_compare.add_argument("original", help="Original image name")
    p_compare.add_argument("slimmed", help="Slimmed image name")
    p_compare.add_argument("--verbose", "-v", action="store_true", help="Show detailed layer comparison")
    p_compare.add_argument("--exclude", "-e", nargs="+", help="Exclude files/directories (glob patterns)")
    p_compare.set_defaults(func=cmd_compare)

    p_dockerfile = subparsers.add_parser("dockerfile", help="Analyze Dockerfile instruction impact")
    p_dockerfile.add_argument("dockerfile", help="Path to Dockerfile")
    p_dockerfile.add_argument("--image", "-i", help="Corresponding image for size estimation")
    p_dockerfile.add_argument("--output", "-o", help="Output optimization suggestions to file")
    p_dockerfile.add_argument("--exclude", "-e", nargs="+", help="Exclude files/directories (glob patterns)")
    p_dockerfile.set_defaults(func=cmd_dockerfile)

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()