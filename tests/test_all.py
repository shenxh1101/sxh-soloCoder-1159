import tempfile, tarfile, io, json, os, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docker_slim.image_parser import ImageParser, WHITEOUT_PREFIX
from docker_slim.layer_analyzer import LayerAnalyzer
from docker_slim.tree_visualizer import TreeVisualizer
from docker_slim.pattern_detector import PatternDetector
from docker_slim.report import Report
from docker_slim.dockerfile_analyzer import DockerfileAnalyzer
from docker_slim.utils import format_size, matches_any_glob, normalize_path

errors = []


def check(name, condition, msg=""):
    if not condition:
        errors.append(f"FAIL [{name}]: {msg}")
        print(f"  ✗ {name}: {msg}")
    else:
        print(f"  ✓ {name}")


def make_tar(path, files):
    with tarfile.open(path, 'w') as t:
        for n, c in files:
            ti = tarfile.TarInfo(name=n)
            ti.size = len(c)
            t.addfile(ti, io.BytesIO(c))


def make_whiteout_tar(path, normal_files, deleted_files):
    """Create a layer tar with normal files plus whiteout markers for deleted files."""
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


def test_whiteout():
    print("\n=== TEST: Whiteout Deletion Detection ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_wh_')

    l1 = os.path.join(tmpdir, 'l1.tar')
    make_tar(l1, [
        ('bin/sh', b'echo hello'),
        ('etc/os-release', b'TestOS'),
        ('var/cache/apt/pkgcache.bin', b'c' * 2000),
        ('var/lib/apt/lists/lock', b'lk' * 200),
        ('usr/lib/libc.so', b'lib' * 500),
    ])

    l2 = os.path.join(tmpdir, 'l2.tar')
    make_whiteout_tar(l2,
        [
            ('app/main.py', b'print(1)' * 200),
            ('var/cache/apt/pkgcache.bin', b'c' * 2000),
            ('var/lib/apt/lists/lock', b'lk' * 200),
            ('usr/lib/libc.so', b'lib' * 500),
        ],
        ['bin/sh', 'etc/os-release']
    )

    l3 = os.path.join(tmpdir, 'l3.tar')
    make_whiteout_tar(l3,
        [('app/config.json', b'{}' * 50)],
        ['var/cache/apt/pkgcache.bin', 'var/lib/apt/lists/lock']
    )

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'FROM ubuntu', 'empty_layer': True},
            {'created_by': 'FROM ubuntu', 'empty_layer': True},
            {'created_by': '/bin/sh -c apt-get install', 'empty_layer': False},
            {'created_by': '/bin/sh -c rm old files', 'empty_layer': False},
            {'created_by': '/bin/sh -c more cleanup', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'], 'Layers': ['l1.tar', 'l2.tar', 'l3.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l1, arcname='l1.tar')
        t.add(l2, arcname='l2.tar')
        t.add(l3, arcname='l3.tar')

    p = ImageParser()
    m = p.parse_from_tar(out_tar)

    check("whiteout-layer2-has-whiteouts", len(m.layers[1].whiteouts) == 2,
          f"Expected 2 whiteouts, got {len(m.layers[1].whiteouts)}")
    check("whiteout-layer2-no-wh-files", '.wh.bin' not in m.layers[1].files and '.wh.etc' not in m.layers[1].files,
          "Whiteout files should not be counted as regular files")
    check("whiteout-layer3-whiteouts", len(m.layers[2].whiteouts) == 2,
          f"Expected 2 whiteouts, got {len(m.layers[2].whiteouts)}")

    a = LayerAnalyzer(m)
    r = a.analyze()

    check("diff-layer1-no-deletions", len(r.layer_diffs[0].deleted) == 0,
          f"Layer 0 should have 0 deletions, got {len(r.layer_diffs[0].deleted)}")
    check("diff-layer2-has-deletions", len(r.layer_diffs[1].deleted) == 2,
          f"Layer 1 should have 2 deletions, got {len(r.layer_diffs[1].deleted)}")

    deleted_paths_layer2 = r.layer_diffs[1].deleted
    check("diff-layer2-deleted-bin-sh", 'bin/sh' in deleted_paths_layer2,
          f"Expected bin/sh deleted, got: {sorted(deleted_paths_layer2)}")
    check("diff-layer2-deleted-etc-os-release", 'etc/os-release' in deleted_paths_layer2,
          f"Expected etc/os-release deleted, got: {sorted(deleted_paths_layer2)}")

    check("diff-layer3-has-deletions", len(r.layer_diffs[2].deleted) >= 2,
          f"Layer 2 should have >=2 deletions, got {len(r.layer_diffs[2].deleted)}")

    check("whiteout-count-match", r.layer_diffs[1].whiteout_count == 2,
          f"whiteout_count should be 2, got {r.layer_diffs[1].whiteout_count}")

    print()
    print("  WHITEOUT TESTS PASSED!" if not any("whiteout" in e for e in errors) else "  WHITEOUT TESTS FAILED!")
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_glob_matching():
    print("\n=== TEST: Glob Pattern Matching ===")

    check("glob-apt-cache-exact", matches_any_glob("var/cache/apt", ["/var/cache/apt"]),
          "var/cache/apt should match /var/cache/apt")
    check("glob-apt-cache-subfile", matches_any_glob("var/cache/apt/pkgcache.bin", ["/var/cache/apt"]),
          "var/cache/apt/pkgcache.bin should match /var/cache/apt")
    check("glob-apt-cache-subdir", matches_any_glob("var/cache/apt/archives/lock", ["/var/cache/apt"]),
          "var/cache/apt/archives/lock should match /var/cache/apt")
    check("glob-apt-lists", matches_any_glob("var/lib/apt/lists/lock", ["/var/lib/apt/lists"]),
          "var/lib/apt/lists/lock should match /var/lib/apt/lists")
    check("glob-pip-cache", matches_any_glob("root/.cache/pip/http/a", ["/root/.cache/pip"]),
          "root/.cache/pip/http/a should match /root/.cache/pip")
    check("glob-npm-cache", matches_any_glob("root/.npm/_cacache/d1", ["/root/.npm"]),
          "root/.npm/_cacache/d1 should match /root/.npm")
    check("glob-yum-cache", matches_any_glob("var/cache/yum/x86_64/7/base/packages", ["/var/cache/yum"]),
          "yum cache should match")

    check("glob-not-match", not matches_any_glob("app/main.py", ["/var/cache/apt"]),
          "app/main.py should NOT match /var/cache/apt")

    check("glob-exclude-nm", matches_any_glob("app/node_modules/express/index.js", ["**/node_modules"]),
          "node_modules should match **/node_modules")
    check("glob-exclude-nm-deep", matches_any_glob("a/b/c/node_modules/x.js", ["**/node_modules"]),
          "deep node_modules should match **/node_modules")
    check("glob-exclude-app-tmp", matches_any_glob("app/tmp/cache/x", ["app/tmp/**"]),
          "app/tmp/** should match app/tmp/cache/x")
    check("glob-exclude-app-tmp-root", matches_any_glob("app/tmp", ["app/tmp/**"]),
          "app/tmp/** should match app/tmp")

    check("glob-not-match-partial", not matches_any_glob("other_app/node_modules", ["app/tmp/**"]),
          "app/tmp/** should NOT match other_app/node_modules")

    print()
    print("  GLOB MATCHING TESTS PASSED!" if not any("glob" in e for e in errors) else "  GLOB MATCHING TESTS FAILED!")


def test_cache_detection():
    print("\n=== TEST: Cache Detection in Analysis ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_cache_')

    l1 = os.path.join(tmpdir, 'l1.tar')
    make_tar(l1, [
        ('var/cache/apt/pkgcache.bin', b'c' * 2000),
        ('var/lib/apt/lists/lock', b'lk' * 500),
        ('var/cache/yum/x86_64/7/base/primary.sqlite', b'yy' * 1000),
        ('root/.cache/pip/http/a', b'pp' * 800),
        ('root/.npm/_cacache/content-v2/sha1/aa/bb', b'nn' * 600),
        ('app/main.py', b'print(1)' * 200),
    ])
    l2 = os.path.join(tmpdir, 'l2.tar')
    make_tar(l2, [('app/config.json', b'{}' * 50), ('app/main.py', b'print(2)' * 200)])

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'RUN apt-get install', 'empty_layer': False},
            {'created_by': 'COPY app', 'empty_layer': False},
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

    detector = PatternDetector(r)
    issues = detector.detect_all()

    cache_issues = [i for i in issues if i.pattern_type == "package_cache"]
    check("cache-issue-found", len(cache_issues) > 0, f"Should detect cache issues, got {len(cache_issues)}")

    if cache_issues:
        cache_issue = cache_issues[0]
        cache_paths = [d[0] for d in cache_issue.details]

        check("cache-apt-subdir", any('var/cache/apt' in p for p in cache_paths),
              f"apt cache should be detected, got: {cache_paths[:5]}")
        check("cache-apt-lists", any('var/lib/apt/lists' in p for p in cache_paths),
              f"apt lists should be detected, got: {cache_paths[:5]}")
        check("cache-pip", any('pip' in p for p in cache_paths),
              f"pip cache should be detected, got: {cache_paths[:5]}")
        check("cache-npm", any('.npm' in p for p in cache_paths),
              f"npm cache should be detected, got: {cache_paths[:5]}")
        check("cache-yum", any('yum' in p for p in cache_paths),
              f"yum cache should be detected, got: {cache_paths[:5]}")
        check("cache-size-positive", cache_issue.size_estimate > 0,
              f"Cache size should be positive, got {cache_issue.size_estimate}")

        print(f"  Cache detected: {format_size(cache_issue.size_estimate)} in Layer {cache_issue.layer_index}")
        print(f"  Cache paths: {[p for p, _ in cache_issue.details[:5]]}")

    print()
    print("  CACHE DETECTION TESTS PASSED!" if not any("cache" in e for e in errors) else "  CACHE DETECTION TESTS FAILED!")
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_exclude_patterns():
    print("\n=== TEST: Exclude Patterns in Full Pipeline ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_excl_')

    l1 = os.path.join(tmpdir, 'l1.tar')
    make_tar(l1, [
        ('app/main.py', b'print(1)' * 200),
        ('app/node_modules/express/index.js', b'x' * 5000),
        ('app/tmp/cache/x', b't' * 3000),
        ('app/data/clean.json', b'{}' * 100),
    ])

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'COPY app', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'], 'Layers': ['l1.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l1, arcname='l1.tar')

    p_full = ImageParser()
    m_full = p_full.parse_from_tar(out_tar)
    a_full = LayerAnalyzer(m_full)
    r_full = a_full.analyze()
    full_size = r_full.total_size

    p_excl = ImageParser(exclude_patterns=["**/node_modules", "app/tmp/**"])
    m_excl = p_excl.parse_from_tar(out_tar)
    a_excl = LayerAnalyzer(m_excl)
    r_excl = a_excl.analyze()
    excl_size = r_excl.total_size

    check("exclude-reduces-size", excl_size < full_size,
          f"Excluded size {excl_size} should be < full size {full_size}")
    check("exclude-nm-file-gone", 'app/node_modules/express/index.js' not in m_excl.layers[0].files,
          "node_modules file should be excluded")
    check("exclude-tmp-file-gone", 'app/tmp/cache/x' not in m_excl.layers[0].files,
          "app/tmp file should be excluded")
    check("exclude-clean-kept", 'app/data/clean.json' in m_excl.layers[0].files,
          "clean.json should NOT be excluded")
    check("exclude-main-kept", 'app/main.py' in m_excl.layers[0].files,
          "main.py should NOT be excluded")

    diff = full_size - excl_size
    print(f"  Full size: {full_size}, Excluded size: {excl_size}, Saved: {diff}")

    print()
    print("  EXCLUDE PATTERN TESTS PASSED!" if not any("exclude" in e for e in errors) else "  EXCLUDE PATTERN TESTS FAILED!")
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_dockerfile_layer_matching():
    print("\n=== TEST: Dockerfile Layer Matching ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_df_')

    l1 = os.path.join(tmpdir, 'l1.tar')
    l2 = os.path.join(tmpdir, 'l2.tar')
    l3 = os.path.join(tmpdir, 'l3.tar')
    make_tar(l1, [('base/file1', b'base' * 500)])
    make_tar(l2, [('usr/bin/app', b'app' * 2000)])
    make_tar(l3, [('etc/config', b'cfg' * 100)])

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': '/bin/sh -c base layer', 'empty_layer': True},
            {'created_by': '/bin/sh -c apt-get install', 'empty_layer': False},
            {'created_by': '/bin/sh -c copy config', 'empty_layer': False},
        ]}, f)

    man = os.path.join(tmpdir, 'manifest.json')
    with open(man, 'w') as f:
        json.dump([{'Config': 'cfg.json', 'RepoTags': ['test:latest'], 'Layers': ['l1.tar', 'l2.tar', 'l3.tar']}], f)

    out_tar = os.path.join(tmpdir, 'img.tar')
    with tarfile.open(out_tar, 'w') as t:
        t.add(man, arcname='manifest.json')
        t.add(cfg, arcname='cfg.json')
        t.add(l1, arcname='l1.tar')
        t.add(l2, arcname='l2.tar')
        t.add(l3, arcname='l3.tar')

    p = ImageParser()
    m = p.parse_from_tar(out_tar)
    a = LayerAnalyzer(m)
    r = a.analyze()

    check("non-base-indices", len(r.non_base_layer_indices) == 2,
          f"Expected 2 non-base layers, got {len(r.non_base_layer_indices)}: {r.non_base_layer_indices}")
    check("non-base-layer-1", r.non_base_layer_indices[0] == 1,
          f"First non-base layer should be index 1, got {r.non_base_layer_indices[0]}")
    check("non-base-layer-2", r.non_base_layer_indices[1] == 2,
          f"Second non-base layer should be index 2, got {r.non_base_layer_indices[1]}")

    dockerfile_path = os.path.join(tmpdir, 'Dockerfile')
    with open(dockerfile_path, 'w') as f:
        f.write("""FROM ubuntu:20.04
RUN apt-get install -y python3
COPY config /etc/
CMD ["python3"]
""")

    dfa = DockerfileAnalyzer(dockerfile_path)
    dfa.parse()
    dfa.estimate_impact(dfa.instructions, r.layer_diffs, r.non_base_layer_indices)

    run_instr = [i for i in dfa.instructions if i.instruction == "RUN"][0]
    copy_instr = [i for i in dfa.instructions if i.instruction == "COPY"][0]

    check("dfa-run-matched", run_instr.matched,
          f"RUN should be matched, got matched={run_instr.matched}")
    check("dfa-copy-matched", copy_instr.matched,
          f"COPY should be matched, got matched={copy_instr.matched}")
    check("dfa-run-layer-index", run_instr.layer_index == 1,
          f"RUN should be Layer 1 (non-base), got {run_instr.layer_index}")
    check("dfa-copy-layer-index", copy_instr.layer_index == 2,
          f"COPY should be Layer 2 (non-base), got {copy_instr.layer_index}")
    check("dfa-run-size", run_instr.estimated_size_added > 0,
          f"RUN should have positive size, got {run_instr.estimated_size_added}")

    print(f"  RUN  -> Layer {run_instr.layer_index}: {format_size(run_instr.estimated_size_added)}")
    print(f"  COPY -> Layer {copy_instr.layer_index}: {format_size(copy_instr.estimated_size_added)}")

    print()
    print("  DOCKERFILE MATCHING TESTS PASSED!" if not any("dfa" in e for e in errors) else "  DOCKERFILE MATCHING TESTS FAILED!")
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_full_report_with_whiteout_and_cache():
    print("\n=== TEST: Full Report Integration ===")
    tmpdir = tempfile.mkdtemp(prefix='dslim_int_')

    l1 = os.path.join(tmpdir, 'l1.tar')
    make_tar(l1, [
        ('var/cache/apt/pkgcache.bin', b'c' * 2000),
        ('var/lib/apt/lists/lock', b'lk' * 500),
        ('root/.cache/pip/http/a', b'pp' * 800),
        ('app/main.py', b'print(1)' * 200),
        ('old_file.txt', b'old' * 1000),
    ])

    l2 = os.path.join(tmpdir, 'l2.tar')
    make_whiteout_tar(l2,
        [('app/config.json', b'{}' * 50)],
        ['old_file.txt', 'var/cache/apt/pkgcache.bin']
    )

    cfg = os.path.join(tmpdir, 'cfg.json')
    with open(cfg, 'w') as f:
        json.dump({'config': {'Image': 'sha:test'}, 'history': [
            {'created_by': 'FROM ubuntu', 'empty_layer': True},
            {'created_by': '/bin/sh -c apt-get install', 'empty_layer': False},
            {'created_by': '/bin/sh -c cleanup', 'empty_layer': False},
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

    report_text = Report(r, 'test:latest').generate_full_report()

    check("report-has-header", "Docker Slim" in report_text, "Report should have header")
    check("report-has-deleted-info", "Deleted" in report_text, "Report should show deleted info")
    check("report-has-cache-section", "package_cache" in report_text, "Report should have cache section")
    check("report-has-cross-layer", "add_delete_cross_layer" in report_text, "Report should have cross-layer deletion")
    check("report-has-savings", "ESTIMATED SAVINGS" in report_text, "Report should have savings section")

    print()
    print("  FULL REPORT INTEGRATION TESTS PASSED!" if not any("report" in e for e in errors) else "  FULL REPORT INTEGRATION TESTS FAILED!")
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_whiteout()
    test_glob_matching()
    test_cache_detection()
    test_exclude_patterns()
    test_dockerfile_layer_matching()
    test_full_report_with_whiteout_and_cache()

    print()
    print("=" * 60)
    if errors:
        print(f"  ❌ {len(errors)} TEST(S) FAILED:")
        for e in errors:
            print(f"     {e}")
    else:
        print("  ✅ ALL TESTS PASSED!")
    print("=" * 60)
    sys.exit(1 if errors else 0)