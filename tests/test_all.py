import tempfile, tarfile, io, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docker_slim.image_parser import ImageParser, WHITEOUT_PREFIX, WHITEOUT_OPAQUE
from docker_slim.layer_analyzer import LayerAnalyzer
from docker_slim.tree_visualizer import TreeVisualizer
from docker_slim.report import Report
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


def make_whiteout_tar(path, normal_files, deleted_files):
    with tarfile.open(path, 'w') as t:
        for n, c in normal_files:
            ti = tarfile.TarInfo(name=n)
            ti.size = len(c)
            t.addfile(ti, io.BytesIO(c))
        for n in deleted_files:
            whitepath = os.path.join(os.path.dirname(n), WHITEOUT_PREFIX + os.path.basename(n))
            ti = tarfile.TarInfo(name=whitepath)
            ti.size = 0
            t.addfile(ti, io.BytesIO(b''))


def make_tar_with_opaque(path, normal_files, opaque_dirs):
    """Create a layer tar with normal files plus opaque whiteout markers."""
    with tarfile.open(path, 'w') as t:
        for n, c in normal_files:
            ti = tarfile.TarInfo(name=n)
            ti.size = len(c)
            t.addfile(ti, io.BytesIO(c))
        for d in opaque_dirs:
            opq_path = os.path.join(d, WHITEOUT_OPAQUE)
            ti = tarfile.TarInfo(name=opq_path)
            ti.size = 0
            t.addfile(ti, io.BytesIO(b''))


# ====================================================
# TEST 1: Whiteout-only deletion (no implicit deletion)
# ====================================================
def test_whiteout_only_deletion():
    print("\n=== TEST 1: Whiteout-only deletion (no implicit) ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_t1_')

    l1 = os.path.join(tmpdir, 'l1.tar')
    make_tar(l1, [
        ('bin/sh', b'echo hello'),
        ('etc/os-release', b'TestOS'),
        ('var/cache/apt/pkgcache.bin', b'c' * 2000),
        ('usr/lib/libc.so', b'lib' * 500),
    ])

    l2 = os.path.join(tmpdir, 'l2.tar')
    make_whiteout_tar(l2,
        [('app/main.py', b'print(1)' * 200)],
        ['bin/sh']
    )

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'FROM ubuntu', 'empty_layer': True},
            {'created_by': '/bin/sh -c setup', 'empty_layer': False},
            {'created_by': '/bin/sh -c rm bin/sh', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'], 'Layers': ['l1.tar', 'l2.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l1, arcname='l1.tar')
        t.add(l2, arcname='l2.tar')

    p = ImageParser()
    m = p.parse_from_tar(out_tar)
    a = LayerAnalyzer(m)
    r = a.analyze()

    # Layer 0: 4 files added, all go to cumulative
    check("t1-l0-files", r.layer_diffs[0].added_file_count == 4, f"got {r.layer_diffs[0].added_file_count}")
    check("t1-l0-del", r.layer_diffs[0].deleted_file_count == 0, f"got {r.layer_diffs[0].deleted_file_count}")
    check("t1-l0-cum", r.cumulative_sizes[0] > 0)

    # Layer 1: whiteout deletes only bin/sh
    # - bin/sh: whiteout deleted
    # - etc/os-release, var/cache/apt/pkgcache.bin, usr/lib/libc.so: NOT in layer 1 tar but NOT deleted (inherited)
    # - app/main.py: new
    check("t1-l1-deleted-only-whiteout", r.layer_diffs[1].deleted_file_count == 1,
          f"Only bin/sh should be deleted, got {r.layer_diffs[1].deleted_file_count}: {sorted(r.layer_diffs[1].deleted)}")
    check("t1-l1-deleted-is-bin-sh", 'bin/sh' in r.layer_diffs[1].deleted)
    check("t1-l1-added", r.layer_diffs[1].added_file_count == 1, f"got {r.layer_diffs[1].added_file_count}")
    check("t1-l1-added-is-app", 'app/main.py' in r.layer_diffs[1].added)

    # Cumulative size should include inherited files + new, minus deleted
    inherited_sizes = sum(m.layers[0].files[p] for p in m.layers[0].files if p not in r.layer_diffs[1].deleted)
    new_size = sum(m.layers[1].files.values())
    expected_cum = (inherited_sizes + new_size)
    check("t1-cum-includes-inherited", r.cumulative_sizes[1] == expected_cum,
          f"cum={r.cumulative_sizes[1]}, expected={expected_cum} (inherited={inherited_sizes}, new={new_size})")

    print(f"  Layer0: +{r.layer_diffs[0].added_size}B, deleted=0, cum={r.cumulative_sizes[0]}")
    print(f"  Layer1: +{r.layer_diffs[1].added_size}B, deleted={r.layer_diffs[1].deleted_file_count}, cum={r.cumulative_sizes[1]}")
    print("  -> Inherited files correctly kept in cumulative size!")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ====================================================
# TEST 2: Dockerfile-instruction-to-layer matching (skip FROM base)
# ====================================================
def test_dockerfile_matching_skip_base():
    print("\n=== TEST 2: Dockerfile matching skip FROM base layers ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_t2_')

    # Simulate: base ubuntu has 5 base layers, user added 2 layers
    l0, l1, l2, l3, l4 = [os.path.join(tmpdir, f'l{i}.tar') for i in range(5)]
    lu0 = os.path.join(tmpdir, 'lu0.tar')
    lu1 = os.path.join(tmpdir, 'lu1.tar')

    make_tar(l0, [('bin/cp', b'c' * 100)])
    make_tar(l1, [('bin/mv', b'm' * 200)])
    make_tar(l2, [('usr/lib/ld.so', b'l' * 500)])
    make_tar(l3, [('etc/hostname', b'h' * 50)])
    make_tar(l4, [('var/log/lastlog', b'g' * 300)])
    make_tar(lu0, [('usr/bin/myapp', b'm' * 3000)])
    make_tar(lu1, [('etc/myapp/config.json', b'{}' * 150)])

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': '/bin/sh -c base', 'empty_layer': True},
            {'created_by': '/bin/sh -c base', 'empty_layer': True},
            {'created_by': '/bin/sh -c base', 'empty_layer': True},
            {'created_by': '/bin/sh -c base', 'empty_layer': True},
            {'created_by': '/bin/sh -c base', 'empty_layer': True},
            {'created_by': '/bin/sh -c apt-get install', 'empty_layer': False},
            {'created_by': '/bin/sh -c copy config', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'],
                     'Layers': ['l0.tar','l1.tar','l2.tar','l3.tar','l4.tar','lu0.tar','lu1.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        for fpath in ['l0.tar','l1.tar','l2.tar','l3.tar','l4.tar','lu0.tar','lu1.tar']:
            t.add(os.path.join(tmpdir, fpath), arcname=fpath)

    p = ImageParser()
    m = p.parse_from_tar(out_tar)
    a = LayerAnalyzer(m)
    r = a.analyze()

    check("t2-base-count", len(r.non_base_layer_indices) == 2,
          f"Expected 2 non-base layers, got {len(r.non_base_layer_indices)}: {r.non_base_layer_indices}")
    check("t2-non-base-5", 5 in r.non_base_layer_indices)
    check("t2-non-base-6", 6 in r.non_base_layer_indices)

    dockerfile_path = os.path.join(tmpdir, 'Dockerfile')
    with open(dockerfile_path, 'w') as f:
        f.write("""FROM ubuntu:20.04
RUN apt-get install -y myapp
COPY config.json /etc/myapp/
CMD ["myapp"]
""")

    dfa = DockerfileAnalyzer(dockerfile_path)
    dfa.parse()
    dfa.estimate_impact(dfa.instructions, r.layer_diffs, r.non_base_layer_indices)

    run_instr = [i for i in dfa.instructions if i.instruction == "RUN"][0]
    copy_instr = [i for i in dfa.instructions if i.instruction == "COPY"][0]
    from_instr = [i for i in dfa.instructions if i.instruction == "FROM"][0]

    check("t2-from-not-zero", not (from_instr.matched and from_instr.estimated_size_added > 0),
          "FROM should not get layer size")
    check("t2-run-matched", run_instr.matched, "RUN should be matched")
    check("t2-copy-matched", copy_instr.matched, "COPY should be matched")

    check("t2-run-layer", run_instr.layer_index == 5,
          f"RUN should be Layer 5 (non-base), got {run_instr.layer_index}")
    check("t2-copy-layer", copy_instr.layer_index == 6,
          f"COPY should be Layer 6 (non-base), got {copy_instr.layer_index}")

    # RUN should show 3000 bytes (usr/bin/myapp), not include base layers
    check("t2-run-size", run_instr.estimated_size_added > 1000,
          f"RUN should show only its own size, got {run_instr.estimated_size_added}")

    print(f"  Base layers: 0-4 (skipped), User layers: 5-6")
    print(f"  RUN  (Layer 5): {format_size(run_instr.estimated_size_added)}")
    print(f"  COPY (Layer 6): {format_size(copy_instr.estimated_size_added)}")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ====================================================
# TEST 3: Opaque whiteout - clears directory contents
# ====================================================
def test_opaque_whiteout():
    print("\n=== TEST 3: Opaque whiteout directory clearing ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_t3_')

    l1 = os.path.join(tmpdir, 'l1.tar')
    make_tar(l1, [
        ('app/cache/a.txt', b'aa' * 500),
        ('app/cache/b.txt', b'bb' * 400),
        ('app/data/clean.json', b'cc' * 100),
        ('lib/shared/libx.so', b'll' * 300),
    ])

    l2 = os.path.join(tmpdir, 'l2.tar')
    make_tar_with_opaque(l2,
        [('app/data/new_config.json', b'nn' * 200)],
        ['app/cache']
    )

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'FROM base', 'empty_layer': True},
            {'created_by': '/bin/sh -c add cache', 'empty_layer': False},
            {'created_by': '/bin/sh -c clear cache dir', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'], 'Layers': ['l1.tar', 'l2.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l1, arcname='l1.tar')
        t.add(l2, arcname='l2.tar')

    p = ImageParser()
    m = p.parse_from_tar(out_tar)

    check("t3-opaque-dir-detected", len(m.layers[1].opaque_dirs) == 1,
          f"Expected 1 opaque dir, got {len(m.layers[1].opaque_dirs)}: {m.layers[1].opaque_dirs}")
    check("t3-opaque-dir-is-cache", 'app/cache' in m.layers[1].opaque_dirs,
          f"Expected app/cache, got {m.layers[1].opaque_dirs}")

    a = LayerAnalyzer(m)
    r = a.analyze()

    # Layer 0: 4 added, 0 deleted
    check("t3-l0-added", r.layer_diffs[0].added_file_count == 4, f"got {r.layer_diffs[0].added_file_count}")

    # Layer 1: opaque dir app/cache deletes a.txt + b.txt from layer 0
    #   new file: app/data/new_config.json
    #   inherited: app/data/clean.json, lib/shared/libx.so
    check("t3-l1-deleted", r.layer_diffs[1].deleted_file_count == 2,
          f"Expected 2 deleted (a.txt + b.txt), got {r.layer_diffs[1].deleted_file_count}: {sorted(r.layer_diffs[1].deleted)}")

    deleted_paths = r.layer_diffs[1].deleted
    check("t3-l1-del-a", 'app/cache/a.txt' in deleted_paths)
    check("t3-l1-del-b", 'app/cache/b.txt' in deleted_paths)

    # Files outside opaque dir should NOT be deleted
    check("t3-l1-not-del-clean", 'app/data/clean.json' not in deleted_paths,
          "app/data/clean.json should NOT be deleted (different dir)")
    check("t3-l1-not-del-lib", 'lib/shared/libx.so' not in deleted_paths,
          "lib/shared/libx.so should NOT be deleted (different dir)")

    check("t3-l1-added", r.layer_diffs[1].added_file_count == 1,
          f"Expected 1 new file, got {r.layer_diffs[1].added_file_count}")
    check("t3-l1-added-config", 'app/data/new_config.json' in r.layer_diffs[1].added)

    # Cumulative size should include inherited + new, minus deleted
    l1_size = r.cumulative_sizes[0]
    l2_deleted_size = sum(m.layers[0].files[p] for p in deleted_paths)
    l2_added_size = r.layer_diffs[1].added_size
    expected_cum2 = l1_size - l2_deleted_size + l2_added_size
    check("t3-cum-correct", r.cumulative_sizes[1] == expected_cum2,
          f"cum={r.cumulative_sizes[1]}, expected={expected_cum2}")

    # opaque_count
    check("t3-opaque-count", r.layer_diffs[1].opaque_count == 2,
          f"opaque_count should be 2 (files deleted), got {r.layer_diffs[1].opaque_count}")

    print(f"  Layer0: +{r.layer_diffs[0].added_size}B, deleted=0, cum={r.cumulative_sizes[0]}")
    print(f"  Layer1: +{r.layer_diffs[1].added_size}B, deleted={r.layer_diffs[1].deleted_file_count} (opaque dir), cum={r.cumulative_sizes[1]}")
    print("  -> Only files under opaque dir removed, sibling directories preserved!")

    # Also run the full report to verify display
    report_text = Report(r, 'test:latest').generate_full_report()
    check("t3-report-has-opaque", "opaque" in report_text.lower(),
          "Report should mention opaque deletions")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ====================================================
# TEST 4: Combined whiteout + opaque + inherited
# ====================================================
def test_combined_whiteout_opaque_inherited():
    print("\n=== TEST 4: Combined whiteout + opaque + inherited ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_t4_')

    l1 = os.path.join(tmpdir, 'l1.tar')
    make_tar(l1, [
        ('bin/sh', b'echo hello'),
        ('bin/ls', b'list'),
        ('usr/local/a.pyc', b'cache' * 100),
        ('var/cache/pkg.bin', b'cache' * 200),
    ])

    l2 = os.path.join(tmpdir, 'l2.tar')
    # whiteout deletes bin/ls; opaque deletes var/cache/ content; new file added
    with tarfile.open(l2, 'w') as t:
        for n, c in [('app/main.py', b'print' * 300)]:
            ti = tarfile.TarInfo(name=n); ti.size = len(c); t.addfile(ti, io.BytesIO(c))
        # whiteout for bin/ls
        ti = tarfile.TarInfo(name='bin/' + WHITEOUT_PREFIX + 'ls'); ti.size = 0; t.addfile(ti, io.BytesIO(b''))
        # opaque marker for var/cache
        ti = tarfile.TarInfo(name='var/cache/' + WHITEOUT_OPAQUE); ti.size = 0; t.addfile(ti, io.BytesIO(b''))

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'FROM base', 'empty_layer': True},
            {'created_by': 'COPY base', 'empty_layer': False},
            {'created_by': 'RUN cleanup', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'], 'Layers': ['l1.tar', 'l2.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l1, arcname='l1.tar')
        t.add(l2, arcname='l2.tar')

    p = ImageParser()
    m = p.parse_from_tar(out_tar)
    a = LayerAnalyzer(m)
    r = a.analyze()

    # Layer 0: 4 files, 0 deleted
    check("t4-l0-added", r.layer_diffs[0].added_file_count == 4)

    # Layer 1:
    #   bin/sh: NOT in l2.tar AND NOT whiteout → INHERITED, NOT deleted
    #   bin/ls: whiteout → DELETED
    #   usr/local/a.pyc: NOT in l2.tar AND NOT whiteout → INHERITED, NOT deleted
    #   var/cache/pkg.bin: opaque dir var/cache/ → DELETED
    #   app/main.py: NEW
    check("t4-l1-deleted-count", r.layer_diffs[1].deleted_file_count == 2,
          f"Expected 2 deleted (ls + pkg.bin), got {r.layer_diffs[1].deleted_file_count}: {sorted(r.layer_diffs[1].deleted)}")

    deleted = r.layer_diffs[1].deleted
    check("t4-l1-del-ls", 'bin/ls' in deleted)
    check("t4-l1-del-pkg", 'var/cache/pkg.bin' in deleted)

    # Inherited files should NOT be in deleted
    check("t4-l1-not-del-sh", 'bin/sh' not in deleted, "bin/sh should be INHERITED, not deleted")
    check("t4-l1-not-del-pyc", 'usr/local/a.pyc' not in deleted, "a.pyc should be INHERITED, not deleted")

    check("t4-l1-added", r.layer_diffs[1].added_file_count == 1)
    check("t4-l1-whiteout-count", r.layer_diffs[1].whiteout_count == 1)
    check("t4-l1-opaque-count", r.layer_diffs[1].opaque_count == 1)

    print(f"  Deleted: {sorted(deleted)}")
    print(f"  Inherited: bin/sh, usr/local/a.pyc")
    print(f"  New: app/main.py")

    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_whiteout_only_deletion()
    test_dockerfile_matching_skip_base()
    test_opaque_whiteout()
    test_combined_whiteout_opaque_inherited()

    print()
    print("=" * 60)
    if errors:
        print(f"  FAILED {len(errors)} test(s):")
        for e in errors:
            print(f"     {e}")
    else:
        print("  ALL TESTS PASSED!")
    print("=" * 60)
    sys.exit(1 if errors else 0)