import tempfile, tarfile, io, json, os, shutil, sys
import os.path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from docker_slim.image_parser import ImageParser
from docker_slim.layer_analyzer import LayerAnalyzer
from docker_slim.tree_visualizer import TreeVisualizer, SORT_ADDED, SORT_CUMULATIVE, SORT_DELETED
from docker_slim.report import Report, FileLifecycleTracker
from docker_slim.dockerfile_analyzer import DockerfileAnalyzer
from docker_slim.utils import format_size

errors = []

def check(name, condition, msg=""):
    if not condition:
        errors.append(f"FAIL [{name}]: {msg}")
        print(f"  X {name}: {msg}")
    else:
        print(f"  OK {name}")


def make_tar(path, files):
    with tarfile.open(path, 'w') as t:
        for n, c in files:
            ti = tarfile.TarInfo(name=n)
            ti.size = len(c)
            t.addfile(ti, io.BytesIO(c))


# ====================================================
# TEST 1: Dockerfile alignment (ubuntu + 1 RUN + 1 COPY)
# User requirements: FROM base is grouped as base region,
# RUN and COPY get correct layer numbers and sizes
# ====================================================
def test_dockerfile_alignment_ubuntu():
    print("\n=== TEST 1: Dockerfile alignment (ubuntu + 1 RUN + 1 COPY) ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_r5_1_')

    # ubuntu base has multiple base layers (all with empty_layer=True)
    # user adds one RUN and one COPY -> two non-empty layers
    layers = []
    for i in range(5):
        l = os.path.join(tmpdir, f'l{i}.tar')
        if i == 0:
            make_tar(l, [('bin/bash', b'bash' * 100), ('etc/passwd', b'root:x:0:0::/root:/bin/bash')])
        elif i == 1:
            make_tar(l, [('usr/bin/ls', b'ls' * 80)])
        elif i == 2:
            make_tar(l, [('usr/bin/cp', b'cp' * 70)])
        elif i == 3:
            make_tar(l, [('usr/lib/x86_64-linux-gnu/libc.so', b'libc' * 2000)])
        elif i == 4:
            make_tar(l, [('var/lib/dpkg/status', b'status' * 300)])
        layers.append(l)

    # User RUN layer (install some packages)
    l_run = os.path.join(tmpdir, 'l_run.tar')
    make_tar(l_run, [
        ('usr/local/bin/myapp', b'myapp' * 4000),
        ('var/cache/apt/pkgcache.bin', b'cache' * 1500),
    ])
    layers.append(l_run)

    # User COPY layer
    l_copy = os.path.join(tmpdir, 'l_copy.tar')
    make_tar(l_copy, [
        ('app/config.json', b'{"port": 8080}' * 100),
    ])
    layers.append(l_copy)

    cfg = os.path.join(tmpdir, 'cfg.json')
    history = []
    # 5 base layers from FROM
    for i in range(5):
        history.append({'created_by': f'/bin/sh -c #(nop)  {i}', 'empty_layer': True})
    # RUN and COPY
    history.append({'created_by': '/bin/sh -c apt-get update && apt-get install -y myapp', 'empty_layer': False})
    history.append({'created_by': '/bin/sh -c #(nop) COPY config.json /app/', 'empty_layer': False})

    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': history}, f)

    layer_paths = [os.path.basename(l) for l in layers]
    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'], 'Layers': layer_paths}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        for l in layers:
            t.add(l, arcname=os.path.basename(l))

    p = ImageParser()
    m = p.parse_from_tar(out_tar)
    a = LayerAnalyzer(m)
    r = a.analyze()

    check("r5-1-non-base-count", len(r.non_base_layer_indices) == 2,
          f"Expected 2 non-base layers (RUN+COPY), got {len(r.non_base_layer_indices)}")
    check("r5-1-non-base-run", 5 in r.non_base_layer_indices, "Layer 5 (RUN) should be non-base")
    check("r5-1-non-base-copy", 6 in r.non_base_layer_indices, "Layer 6 (COPY) should be non-base")

    dockerfile_path = os.path.join(tmpdir, 'Dockerfile')
    with open(dockerfile_path, 'w') as f:
        f.write("""FROM ubuntu:22.04
RUN apt-get update && apt-get install -y myapp
COPY config.json /app/
CMD ["myapp"]
""")

    dfa = DockerfileAnalyzer(dockerfile_path)
    dfa.parse()
    dfa.estimate_impact(dfa.instructions, r.layer_diffs, r.non_base_layer_indices)

    run_instr = [i for i in dfa.instructions if i.instruction == "RUN"][0]
    copy_instr = [i for i in dfa.instructions if i.instruction == "COPY"][0]

    check("r5-1-run-matched", run_instr.matched, "RUN should be matched")
    check("r5-1-copy-matched", copy_instr.matched, "COPY should be matched")
    check("r5-1-run-layer", run_instr.layer_index == 5, f"RUN should be on layer 5, got {run_instr.layer_index}")
    check("r5-1-copy-layer", copy_instr.layer_index == 6, f"COPY should be on layer 6, got {copy_instr.layer_index}")

    expected_run_size = 20000 + 7500
    check("r5-1-run-size", run_instr.estimated_size_added == expected_run_size,
          f"RUN size should be {expected_run_size}, got {run_instr.estimated_size_added}")
    expected_copy_size = len(b'{"port": 8080}' * 100)
    check("r5-1-copy-size", copy_instr.estimated_size_added == expected_copy_size,
          f"COPY size should be {expected_copy_size}, got {copy_instr.estimated_size_added}")

    report = Report(r, 'ubuntu-test', dfa=dfa)
    full_text = report.generate_full_report()
    json_text = report.generate_json_report()
    data = json.loads(json_text)

    check("r5-1-report-has-alignment", "Dockerfile <-> Image Layer Alignment" in full_text)
    check("r5-1-report-base-region", "base" in full_text)
    check("r5-1-report-json-layers", "layers" in data and len(data["layers"]) == 7)
    check("r5-1-report-json-dockerfile", "dockerfile_analysis" in data and len(data["dockerfile_analysis"]) == 4)

    print(f"  FROM: base layers 0-4 (inherited, grouped to base region)")
    print(f"  RUN:  Layer {run_instr.layer_index}, size={format_size(run_instr.estimated_size_added)} ✓")
    print(f"  COPY: Layer {copy_instr.layer_index}, size={format_size(copy_instr.estimated_size_added)} ✓")
    print(f"  JSON export created ✓")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ====================================================
# TEST 2: File lifecycle tracking - added then deleted
# ====================================================
def test_file_lifecycle_added_deleted():
    print("\n=== TEST 2: File lifecycle - added then deleted ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_r5_2_')

    # Layer 0: add big file
    l0 = os.path.join(tmpdir, 'l0.tar')
    make_tar(l0, [
        ('big_file.tar.gz', b'x' * 10000),
        ('base.txt', b'base'),
    ])

    # Layer 1: delete big_file.tar.gz via whiteout, add new file
    l1 = os.path.join(tmpdir, 'l1.tar')
    with tarfile.open(l1, 'w') as t:
        ti = tarfile.TarInfo(name='big_file.tar.gz')
        ti.size = 0
        t.addfile(ti, io.BytesIO(b''))
        from docker_slim.image_parser import WHITEOUT_PREFIX
        ti = tarfile.TarInfo(name=WHITEOUT_PREFIX + 'big_file.tar.gz')
        ti.size = 0
        t.addfile(ti, io.BytesIO(b''))
        ti = tarfile.TarInfo(name='new_file.txt')
        data = b'new' * 100
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'ADD big_file', 'empty_layer': False},
            {'created_by': 'RM big_file', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'], 'Layers': ['l0.tar', 'l1.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l0, arcname='l0.tar')
        t.add(l1, arcname='l1.tar')

    p = ImageParser()
    m = p.parse_from_tar(out_tar)
    a = LayerAnalyzer(m)
    r = a.analyze()

    tracker = FileLifecycleTracker(r)
    tracker.build()
    wasteful = tracker.get_wasteful_files()

    check("r5-2-wasteful-has-big", len(wasteful) >= 1, "Should detect at least one wasteful file")
    path, waste, events = wasteful[0]
    check("r5-2-big-is-waste", path == "big_file.tar.gz", f"Expected big_file.tar.gz, got {path}")
    check("r5-2-waste-correct", waste == 10000, f"Expected 10000 bytes waste, got {waste}")
    check("r5-2-two-events", len(events) == 2, f"Expected 2 events (add + delete), got {len(events)}")

    event_types = [e.event_type for e in events]
    check("r5-2-events-correct", "added" in event_types and "deleted" in event_types,
          f"Events should be add -> delete, got {event_types}")

    report = Report(r, 'test')
    full_text = report.generate_full_report()
    check("r5-2-report-has-lifecycle", "FILE LIFECYCLE VIEW" in full_text)
    check("r5-2-report-has-bigfile", "big_file.tar.gz" in full_text)

    print(f"  File big_file.tar.gz:")
    print(f"    Added at Layer 0 ({format_size(10000)})")
    print(f"    Deleted at Layer 1 (via whiteout)")
    print(f"    Total waste: {format_size(waste)} ✓")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ====================================================
# TEST 3: Tree sorting by added / cumulative / deleted
# ====================================================
def test_tree_sorting():
    print("\n=== TEST 3: Tree sorting by added / cumulative / deleted ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_r5_3_')

    l0 = os.path.join(tmpdir, 'l0.tar')
    make_tar(l0, [('a.txt', b'a' * 1000)])
    l1 = os.path.join(tmpdir, 'l1.tar')
    make_tar(l1, [('b.txt', b'b' * 5000)])
    l2 = os.path.join(tmpdir, 'l2.tar')
    from docker_slim.image_parser import WHITEOUT_PREFIX
    with tarfile.open(l2, 'w') as t:
        ti = tarfile.TarInfo(name=WHITEOUT_PREFIX + 'a.txt')
        ti.size = 0
        t.addfile(ti, io.BytesIO(b''))
        ti = tarfile.TarInfo(name='c.txt')
        ti.size = 2000
        t.addfile(ti, io.BytesIO(b'c' * 2000))

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'L0', 'empty_layer': False},
            {'created_by': 'L1', 'empty_layer': False},
            {'created_by': 'L2', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'],
                     'Layers': ['l0.tar', 'l1.tar', 'l2.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l0, arcname='l0.tar')
        t.add(l1, arcname='l1.tar')
        t.add(l2, arcname='l2.tar')

    p = ImageParser()
    m = p.parse_from_tar(out_tar)
    a = LayerAnalyzer(m)
    r = a.analyze()

    tv_added = TreeVisualizer(r, sort_by=SORT_ADDED)
    sorted_added = tv_added._sorted_indices()
    check("r5-3-sort-added-order", sorted_added[0] == 1, f"First should be layer 1 (5000 added), got {sorted_added[0]}")

    tv_del = TreeVisualizer(r, sort_by=SORT_DELETED)
    sorted_del = tv_del._sorted_indices()
    check("r5-3-sort-deleted-order", sorted_del[0] == 2, f"First should be layer 2 (deleted 1000), got {sorted_del[0]}")

    text_added = tv_added.render()
    text_del = tv_del.render()
    lines_added = text_added.splitlines()
    check("r5-3-render-added", any("Layer 1" in line for line in lines_added))
    check("r5-3-render-deleted", any("reclaim" in line.lower() for line in text_del.splitlines()))

    table = tv_added.render_summary_table()
    lines_table = table.splitlines()
    layer_lines = [l for l in lines_table if l.strip() and any(str(i) in l[:6] for i in [0,1,2])]
    check("r5-3-summary-sorted", len(layer_lines) == 3, f"Should have 3 layer lines, got {len(layer_lines)}")

    print(f"  Sort by added: order {sorted_added} (largest added first) ✓")
    print(f"  Sort by deleted: order {sorted_del} (most reclaim first) ✓")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ====================================================
# TEST 4: JSON report contains all requested fields
# ====================================================
def test_json_report_export():
    print("\n=== TEST 4: JSON report export full structure ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_r5_4_')

    l0 = os.path.join(tmpdir, 'l0.tar')
    make_tar(l0, [
        ('var/cache/apt/pkgcache.bin', b'cache' * 2000),
        ('app/main.py', b'main' * 500),
    ])
    l1 = os.path.join(tmpdir, 'l1.tar')
    from docker_slim.image_parser import WHITEOUT_PREFIX as WP
    with tarfile.open(l1, 'w') as t:
        target = 'var/cache/apt/pkgcache.bin'
        dname = target.replace('/', os.sep) if os.sep != '/' else target
        parent, fname = os.path.split(dname)
        whitepath = os.path.join(parent, WP + fname).replace(os.sep, '/')
        ti = tarfile.TarInfo(name=whitepath)
        ti.size = 0
        t.addfile(ti, io.BytesIO(b''))
        data = b'js' * 1000
        ti = tarfile.TarInfo(name='app/static.js')
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))

    dockerfile_path = os.path.join(tmpdir, 'Dockerfile')
    with open(dockerfile_path, 'w') as f:
        f.write("""FROM alpine
RUN apk add --no-cache python3
COPY . /app/
""")

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'FROM alpine', 'empty_layer': True},
            {'created_by': '/bin/sh -c apk add && COPY .', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'],
                     'Layers': ['l0.tar', 'l1.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l0, arcname='l0.tar')
        t.add(l1, arcname='l1.tar')

    p = ImageParser()
    m = p.parse_from_tar(out_tar)
    a = LayerAnalyzer(m)
    r = a.analyze()

    dfa = DockerfileAnalyzer(dockerfile_path)
    dfa.parse()
    dfa.estimate_impact(dfa.instructions, r.layer_diffs, r.non_base_layer_indices)

    report = Report(r, 'test-image:latest', dfa=dfa)
    json_str = report.generate_json_report()
    data = json.loads(json_str)

    required_fields = [
        'image', 'total_layers', 'total_size_bytes', 'total_size',
        'non_base_layer_indices', 'layers', 'issues', 'savings',
        'file_lifecycle', 'dockerfile_analysis',
    ]
    for f in required_fields:
        check(f"r5-4-has-{f}", f in data, f"Missing required field {f}")

    check("r5-4-layers-correct", len(data['layers']) == 2, f"Expected 2 layers, got {len(data['layers'])}")
    check("r5-4-non-base-correct", len(data['non_base_layer_indices']) == 1,
          f"Expected 1 non-base, got {len(data['non_base_layer_indices'])}")

    layer0 = data['layers'][0]
    check("r5-4-layer-fields", all(k in layer0 for k in [
        'index', 'layer_id', 'is_user_layer', 'added_file_count',
        'added_size_bytes', 'added_size', 'deleted_file_count',
        'cumulative_size_bytes', 'cumulative_size', 'top_added_files',
        'deleted_paths', 'created_by',
    ]), "Layer missing required fields")

    check("r5-4-dockerfile-analysis", len(data['dockerfile_analysis']) == 3,
          f"Expected 3 instructions in dockerfile analysis, got {len(data['dockerfile_analysis'])}")

    has_waste = any(l['path'] == 'var/cache/apt/pkgcache.bin' for l in data['file_lifecycle'])
    check("r5-4-lifecycle-has-deleted", has_waste, "Should have the deleted file in file_lifecycle")

    print(f"  JSON structure: {len(required_fields)} top-level fields ✓")
    print(f"  Layers: {len(data['layers'])} entries with full stats ✓")
    print(f"  Dockerfile: {len(data['dockerfile_analysis'])} instructions with matching ✓")
    print(f"  File lifecycle: {len(data['file_lifecycle'])} wasteful entries ✓")

    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_dockerfile_alignment_ubuntu()
    test_file_lifecycle_added_deleted()
    test_tree_sorting()
    test_json_report_export()

    print()
    print("=" * 60)
    if errors:
        print(f"  FAILED {len(errors)} test(s):")
        for e in errors:
            print(f"     {e}")
    else:
        print("  ALL Round 5 TESTS PASSED!")
    print("=" * 60)
    sys.exit(1 if errors else 0)
